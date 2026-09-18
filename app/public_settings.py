"""The common export boundary for settings, including nested extension data."""
SECRET_MARKERS = ('password', 'secret', 'token', 'api_key', 'apikey', 'database_url',
                  'connection', 'oauth', 'webhook')
LOCAL_SETTINGS_KEYS = {'clock_recovery', 'clock_recovery_epoch'}


def public_settings(value):
    if isinstance(value, dict):
        return {str(k): public_settings(v) for k, v in value.items()
                if k not in LOCAL_SETTINGS_KEYS and not any(m in str(k).casefold() for m in SECRET_MARKERS)}
    if isinstance(value, list):
        return [public_settings(v) for v in value]
    return value
