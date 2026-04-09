from .translations import TRANSLATIONS


def language_context(request):
    """Inject translation dict `t`, `lang`, and `is_rtl` into every template."""
    lang = request.session.get('lang', 'en')
    if lang not in TRANSLATIONS:
        lang = 'en'
    return {
        't': TRANSLATIONS[lang],
        'lang': lang,
        'is_rtl': lang == 'ar',
    }
