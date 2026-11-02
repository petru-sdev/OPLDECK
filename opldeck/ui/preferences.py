"""Persistência compartilhada das preferências da interface."""

from PySide6.QtCore import QSettings


def application_settings():
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "OPLDeck", "OPLDeck")


def save_preferences(settings, values):
    previous = {key: (settings.contains(key), settings.value(key)) for key in values}
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        for key, (existed, value) in previous.items():
            if existed:
                settings.setValue(key, value)
            else:
                settings.remove(key)
        raise OSError("Não foi possível salvar as preferências. Verifique a permissão da pasta de configurações.")
