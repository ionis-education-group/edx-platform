"""Middleware classes for third_party_auth."""

import logging
import requests

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.contrib.auth.models import AnonymousUser
from student.models import UserProfile

from social.apps.django_app.default import models
from social.apps.django_app.middleware import SocialAuthExceptionMiddleware

from . import pipeline

log = logging.getLogger(__file__)


class ExceptionMiddleware(SocialAuthExceptionMiddleware):
    """Custom middleware that handles conditional redirection."""

    def get_redirect_uri(self, request, exception):
        # Fall back to django settings's SOCIAL_AUTH_LOGIN_ERROR_URL.
        redirect_uri = super(ExceptionMiddleware, self).get_redirect_uri(request, exception)

        # Safe because it's already been validated by
        # pipeline.parse_query_params. If that pipeline step ever moves later
        # in the pipeline stack, we'd need to validate this value because it
        # would be an injection point for attacker data.
        auth_entry = request.session.get(pipeline.AUTH_ENTRY_KEY)

        # Check if we have an auth entry key we can use instead
        if auth_entry and auth_entry in pipeline.AUTH_DISPATCH_URLS:
            redirect_uri = pipeline.AUTH_DISPATCH_URLS[auth_entry]

        return redirect_uri


class PortalSynchronizerMiddleware(object):
    """Custom middleware to synchronize user status of LMS with Portal provider."""

    def process_request(self, request):
        if request.user.is_authenticated():
            user = request.user
            social_auth = models.DjangoStorage.user.get_social_auth_for_user(user)

            if len(social_auth) == 1:
                social_data = social_auth[0]

                try:
                    r = requests.get(
                        settings.IONISX_AUTH.get('USER_DATA_URL'),
                        headers={'Authorization': 'Bearer {0}'.format(social_data.extra_data['access_token'])}
                    )
                except requests.ConnectionError as err:
                    log.warning(err)
                    return

                try:
                    body = r.json()
                except ValueError:
                    body = None

                if r.status_code != 200:
                    if body and u'error' in body and u'redirectTo' in body[u'error']:
                        return redirect(body[u'error'][u'redirectTo'])
                    else:
                        return logout(request)

                if body:
                    id_ = body['id']
                    email = body['primaryEmail']
                    username = body['username']
                    name = body['name']

                    if (user.email != email or user.username != body['username']):
                        log.info('User {} needs to be updated'.format(id_))
                        user.email = email
                        user.username = username
                        user.save()

                    if user.profile.name != body['name']:
                        log.info('User profile for {} needs to be updated'.format(id_))
                        user.profile.name = name
                        user.profile.save()
