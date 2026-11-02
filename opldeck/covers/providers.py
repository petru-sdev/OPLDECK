"""Fontes de capas por ID: catálogo interno e endereços HTTPS cadastrados."""

from dataclasses import asdict, dataclass
import json
import re
from string import Formatter
from urllib.parse import urlsplit
from uuid import uuid4

from opldeck.core.formats import validate_game_id


@dataclass(frozen=True)
class CoverProvider:
    key: str
    name: str
    homepage: str
    template: str

    def cover_url(self, game_id):
        validate_game_id(game_id)
        serial = game_id.replace("_", "-").replace(".", "")
        return self.template.format(serial=serial, id=game_id)


def custom_provider(name, template, *, key=None):
    name, template = name.strip(), template.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Informe um nome de fonte com até 80 caracteres.')
    if len(template) > 2048 or any(c.isspace() or ord(c) < 32 for c in template):
        raise ValueError('O endereço deve ter até 2048 caracteres, sem espaços.')
    try:
        fields = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError('Marcadores inválidos no endereço da capa.') from exc
    placeholders = [field for _, field, _, _ in fields if field is not None]
    if not placeholders or any(field not in ('id', 'serial') or spec or conversion
                               for _, field, spec, conversion in fields if field is not None):
        raise ValueError('Use {id} ou {serial} no endereço da imagem, sem outros marcadores.')
    parsed = urlsplit(template)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Porta inválida no endereço.') from exc
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or '{' in parsed.netloc or '}' in parsed.netloc
            or '\\' in template):
        raise ValueError('Use um endereço HTTPS completo, sem login, fragmento ou marcador no domínio.')
    if key is not None and not re.fullmatch(r'custom_[0-9a-f]{32}', key):
        raise ValueError('Identificador de fonte inválido.')
    return CoverProvider(key or 'custom_' + uuid4().hex, name,
                         f'https://{parsed.netloc}', template)


PROVIDERS = {
    "xlenore": CoverProvider(
        "xlenore", "xlenore / PS2 Covers", "https://github.com/xlenore/ps2-covers",
        "https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/{serial}.jpg",
    ),
}


@dataclass(frozen=True)
class ProviderPreferences:
    enabled: bool = False
    provider_id: str = "xlenore"
    custom: tuple[CoverProvider, ...] = ()

    @property
    def catalog(self):
        return {**PROVIDERS, **{provider.key: provider for provider in self.custom}}

    @property
    def alternatives(self):
        return [provider for key, provider in self.catalog.items() if key != self.provider_id]

    @classmethod
    def load(cls, settings):
        key = settings.value("providers/cover", "xlenore")
        enabled = settings.value("providers/enabled", False)
        try:
            raw = settings.value('providers/custom', '[]')
            if not isinstance(raw, str) or len(raw) > 131072:
                raise ValueError('Cadastro de fontes inválido.')
            data = json.loads(raw)
            if not isinstance(data, list) or len(data) > 32:
                raise ValueError('Cadastro de fontes inválido.')
            custom = tuple(custom_provider(item['name'], item['template'], key=item['key']) for item in data)
            validate_catalog(custom)
        except (ValueError, TypeError, KeyError, AttributeError):
            return cls()
        # Valores desconhecidos não habilitam acesso de rede implicitamente.
        if not isinstance(key, str) or key not in {**PROVIDERS, **{p.key: p for p in custom}}:
            return cls(custom=custom)
        return cls(enabled is True or isinstance(enabled, str) and enabled.lower() == "true", key, custom)

    def values(self):
        validate_catalog(self.custom)
        if self.provider_id not in self.catalog:
            raise ValueError("Provedor de capas desconhecido.")
        return {"providers/enabled": self.enabled, "providers/cover": self.provider_id,
                'providers/custom': json.dumps([asdict(provider) for provider in self.custom], ensure_ascii=False)}


def validate_catalog(custom):
    if len(custom) > 32:
        raise ValueError('É possível cadastrar até 32 fontes adicionais.')
    keys, names, templates = set(PROVIDERS), {p.name.casefold() for p in PROVIDERS.values()}, {p.template for p in PROVIDERS.values()}
    for provider in custom:
        custom_provider(provider.name, provider.template, key=provider.key)
        if provider.key in keys or provider.name.casefold() in names or provider.template in templates:
            raise ValueError('Já existe uma fonte com esse nome ou endereço.')
        keys.add(provider.key)
        names.add(provider.name.casefold())
        templates.add(provider.template)
