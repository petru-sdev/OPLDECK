"""Entrada gráfica e comandos de diagnóstico sem Qt."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__
from .branding import APP_NAME
from opldeck.core.binimage import convert_bin, inspect_bin
from opldeck.core.crc import usba_crc32
from opldeck.core.conversion import CancelledError, export_ul, install_iso
from opldeck.core.formats import FormatError, Media
from opldeck.library.fileio import regular_reader
from opldeck.core.iso import inspect_iso
from opldeck.library.management import delete_ul, recover_ul, rename_ul
from opldeck.library.recovery import choices_from_plan, rebuild_ul, scan_ul
from opldeck.core.ul import read_cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="Abrir interface gráfica")
    iso = commands.add_parser("inspect", help="Identificar uma ISO de PS2")
    iso.add_argument("path", type=Path)
    ul = commands.add_parser("list-ul", help="Ler os registros de ul.cfg")
    ul.add_argument("path", type=Path)
    crc = commands.add_parser("crc", help="Calcular CRC USBExtreme de um título ASCII")
    crc.add_argument("title")
    install = commands.add_parser("install", help="Converter ISO para UL em um diretório existente")
    install.add_argument("path", type=Path)
    install.add_argument("destination", type=Path)
    install.add_argument("--title", help="Título UL ASCII de até 32 caracteres")
    install.add_argument("--media", choices=["CD", "DVD"], help="Obrigatório fora das pastas CD/DVD")
    export = commands.add_parser("export", help="Reconstruir uma ISO a partir de ul.cfg")
    export.add_argument("cfg", type=Path)
    export.add_argument("game_id", help="Exemplo: SLUS_123.45")
    export.add_argument("destination", type=Path)
    bin_inspect = commands.add_parser("inspect-bin", help="Identificar BIN raw ou CUE de faixa única")
    bin_inspect.add_argument("path", type=Path)
    bin_convert = commands.add_parser("bin2iso", help="Converter BIN/CUE de PS2 para uma nova ISO")
    bin_convert.add_argument("path", type=Path)
    bin_convert.add_argument("destination", type=Path)
    rename = commands.add_parser("rename-ul", help="Renomear título, CRC e partes de um jogo UL")
    rename.add_argument("cfg", type=Path)
    rename.add_argument("game_id")
    rename.add_argument("title")
    delete = commands.add_parser("delete-ul", help="Excluir um jogo UL e atualizar o índice")
    delete.add_argument("cfg", type=Path)
    delete.add_argument("game_id")
    delete.add_argument("--yes", action="store_true", required=True, help="Confirmar exclusão permanente das partes")
    recover = commands.add_parser("recover-ul", help="Resolver operação de gerenciamento UL interrompida")
    recover.add_argument("directory", type=Path)
    scan = commands.add_parser("scan-ul", help="Analisar partes UL e preparar recuperação de ul.cfg")
    scan.add_argument("directory", type=Path)
    scan.add_argument("--output", type=Path, help="Salvar plano editável em um novo arquivo .json")
    rebuild = commands.add_parser("rebuild-ul", help="Reconstruir ul.cfg a partir de um plano revisado")
    rebuild.add_argument("directory", type=Path)
    rebuild.add_argument("--plan", type=Path, required=True)
    rebuild.add_argument("--yes", action="store_true", required=True, help="Confirmar os jogos incluídos no novo índice")
    rebuild.add_argument("--replace", action="store_true", help="Autorizar substituição do índice existente com backup")
    args = parser.parse_args(argv)
    last_percent = -5

    def report(event):
        nonlocal last_percent
        if getattr(event, 'phase', '') == 'sync':
            print('Sincronizando…', file=sys.stderr)
            return
        percent = event.completed * 100 // event.total
        if percent >= last_percent + 5 or percent == 100:
            if args.command == "rebuild-ul":
                print(f"{percent:3}% | registros {event.completed}/{event.total}", file=sys.stderr)
                last_percent = percent
                return
            unit = (f"setores {event.completed // 2048}/{event.total // 2048}" if args.command == "bin2iso"
                    else f"parte {event.part}/{event.parts}")
            print(f"{percent:3}% | {event.completed}/{event.total} bytes | "
                  f"{unit}", file=sys.stderr)
            last_percent = percent
    try:
        if args.command == "crc":
            print(f"{usba_crc32(args.title):08X}")
        elif args.command == "inspect":
            result = asdict(inspect_iso(args.path))
            result["path"] = str(result["path"])
            result["media"] = result["media"].name
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "list-ul":
            print(json.dumps([
                {"title": e.title, "game_id": e.game_id, "parts": e.parts,
                 "media": e.media.name, "crc": f"{e.crc:08X}"}
                for e in read_cfg(args.path)
            ], ensure_ascii=False, indent=2))
        elif args.command == "install":
            entry = install_iso(args.path, args.destination, title=args.title,
                                media=Media[args.media] if args.media else None, progress=report)
            print(json.dumps({"game_id": entry.game_id, "title": entry.title,
                              "parts": entry.parts, "crc": f"{entry.crc:08X}",
                              "cfg": str(args.destination / "ul.cfg")}, ensure_ascii=False))
        elif args.command == "export":
            result = export_ul(args.cfg, args.game_id.upper(), args.destination, progress=report)
            print(json.dumps({"iso": str(result)}, ensure_ascii=False))
        elif args.command == "inspect-bin":
            game = inspect_bin(args.path)
            result = asdict(game)
            result["path"] = str(result["path"])
            result["bin_path"] = str(result["bin_path"])
            result["sector_format"] = game.sector_format
            result["validation"] = "Identificação; todos os setores serão verificados estruturalmente na conversão. EDC/ECC não verificados."
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "bin2iso":
            result = convert_bin(args.path, args.destination, progress=report)
            print(json.dumps({"iso": str(result)}, ensure_ascii=False))
        elif args.command in ("rename-ul", "delete-ul"):
            if args.command == "rename-ul":
                entry = rename_ul(args.cfg, args.game_id.upper(), args.title, progress=report)
            else:
                entry = delete_ul(args.cfg, args.game_id.upper(), progress=report)
            print(json.dumps({"operation": args.command, "game_id": entry.game_id,
                              "title": entry.title, "crc": f"{entry.crc:08X}",
                              "cfg": str(args.cfg)}, ensure_ascii=False))
        elif args.command == "recover-ul":
            print(json.dumps({"result": recover_ul(args.directory)}, ensure_ascii=False))
        elif args.command == "scan-ul":
            result = json.dumps(scan_ul(args.directory).to_plan(), ensure_ascii=False, indent=2)
            if args.output:
                if args.output.suffix.lower() != ".json":
                    raise FormatError("O plano deve ser salvo em um novo arquivo .json.")
                with args.output.open("x", encoding="utf-8") as output:
                    output.write(result + "\n")
            print(result)
        elif args.command == "rebuild-ul":
            with regular_reader(args.plan) as source:
                data = source.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise FormatError("Plano JSON excede 64 MiB.")
            try:
                plan = json.loads(data)
            except (ValueError, UnicodeError) as exc:
                raise FormatError(f"Plano JSON inválido: {exc}") from exc
            scan = scan_ul(args.directory)
            result = rebuild_ul(scan, choices_from_plan(scan, plan), replace_existing=args.replace, progress=report)
            print(json.dumps({"cfg": str(result.cfg), "games": result.games, "excluded": result.excluded,
                              "backup": str(result.backup) if result.backup else None}, ensure_ascii=False))
        else:
            try:
                from opldeck.ui.gui import run
            except ModuleNotFoundError as exc:
                if exc.name and exc.name.startswith("PySide6"):
                    parser.exit(1, "Instale a interface com: python3 -m pip install -e .\n")
                raise
            return run()
    except KeyboardInterrupt as exc:
        print("Operação interrompida; confira abaixo eventuais pendências de recuperação.", file=sys.stderr)
        for note in getattr(exc, "__notes__", []):
            print(note, file=sys.stderr)
        return 130
    except (OSError, FormatError, CancelledError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        for note in getattr(exc, "__notes__", []):
            print(note, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
