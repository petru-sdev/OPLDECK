"""Diagnósticos de nomes e estrutura OPL; não altera arquivos ou dispositivos."""

import os
from pathlib import Path

ISO_TITLE_BYTES = 160


def iso_name_parts(path):
    """Mirror OPL's legacy-name detection using byte offsets, not Unicode indices."""
    name = os.fsencode(Path(path).name)
    legacy = len(name) >= 17 and name[4:5] == b'_' and name[8:9] == b'.' and name[11:12] == b'.'
    return (os.fsdecode(name[12:-4]), os.fsdecode(name[:11])) if legacy else (Path(path).stem, '')


def iso_warnings(path, game_id):
    path = Path(path)
    title, startup = iso_name_parts(path)
    warnings = []
    if startup and startup != game_id:
        warnings.append(f'ID no nome difere do SYSTEM.CNF: nome={startup}; interno={game_id}. '
                        f'O Deck usa o ID interno; no nome antigo o OPL usa {startup}. '
                        'Confira o ID e corrija o nome antes de usar a ISO diretamente no OPL.')
    length = len(os.fsencode(title))
    if length > ISO_TITLE_BYTES:
        warnings.append(f'Título ISO com {length} bytes; o OPL aceita até {ISO_TITLE_BYTES}. '
                        'Encurte o título para a ISO aparecer no console.')
    if any(ord(char) < 32 or ord(char) == 127 or char in '<>:"\\|?*' for char in path.name):
        warnings.append('Nome com caracteres incompatíveis com FAT/exFAT. '
                        'Remova controles e os caracteres < > : " \\ | ? * antes de copiar a ISO.')
    if title.endswith((' ', '.')):
        warnings.append('Título termina com espaço ou ponto; ajuste o nome para evitar incompatibilidades.')
    folder = path.parent.name
    if folder.upper() not in {'CD', 'DVD'}:
        warnings.append('ISO fora de CD/DVD. Pode ser convertida para UL; para uso direto no OPL, '
                        'coloque-a em CD/ ou DVD/ na raiz da biblioteca, conforme a mídia.')
    elif folder not in {'CD', 'DVD'}:
        warnings.append(f'Pasta {folder!r}: use o nome {folder.upper()} para seguir a estrutura OPL. '
                        'A distinção de maiúsculas depende do destino.')
    return tuple(warnings)
