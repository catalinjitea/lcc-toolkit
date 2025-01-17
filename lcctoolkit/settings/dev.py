import environ
from lcctoolkit.settings.base import *  # noqa


root = environ.Path(__file__) - 3
env = environ.Env(DEBUG=(bool, False),)

BASE_DIR = root()

SECRET_KEY = env('SECRET_KEY', default='secret')
DEBUG = env('DEBUG')

DATABASES = {
    'default': env.db(default='sqlite:////tmp/my-tmp-sqlite.db'),
}

ELASTICSEARCH_DSL = {
    'default': {
        'hosts': env(
            'ELASTICSEARCH_HOST', default='elastic:changeme@elasticsearch:9200')
    },
}

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

STATIC_URL = '/static/'
STATIC_ROOT = env('STATIC_ROOT',
                  default=root.path('lcc/static')())

MEDIA_URL = '/files/'
MEDIA_ROOT = root.path('media/uploadfiles/')()

EMAIL_HOST = 'postfix'
EMAIL_FROM = 'noreply-toolkit@eaudeweb.ro'

DOMAIN = env('DOMAIN', default='http://localhost:8000')

if not DEBUG:

    # sentry configuration
    SENTRY_PUBLIC_DSN = env('SENTRY_PUBLIC_DSN', default='')
    SENTRY_DSN = env('SENTRY_DSN', default='')
    RAVEN_CONFIG = {'dsn': SENTRY_DSN}
    INSTALLED_APPS += ('raven.contrib.django.raven_compat',)

    # google analytics
    GA_TRACKING_ID = env('GA_TRACKING_ID', default='')
