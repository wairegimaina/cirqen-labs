"""
ASGI config for Equiper project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Equiper.settings')
# dash.plotty to be used later incase of more analyitical details
application = get_asgi_application()
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from django_plotly_dash.routing import application as dash_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Equiper.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": URLRouter([
        path('dpd/ws/channel', dash_application),
    ])
})