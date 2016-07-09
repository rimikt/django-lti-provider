from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.urlresolvers import reverse
from django.http.response import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render_to_response
from django.template.context import RequestContext
from django.utils.decorators import method_decorator
from django.views.generic.base import View, TemplateView

from lti_auth.lti import LTI
from lti_auth.models import LTICourseContext


class LTIAuthMixin(object):
    role_type = 'any'
    request_type = 'any'

    def join_groups(self, lti, ctx, user):
        # add the user to the requested groups
        user.groups.add(ctx.group)
        for role in lti.user_roles():
            role = role.lower()
            if ('staff' in role or
                'instructor' in role or
                    'administrator' in role):
                user.groups.add(ctx.faculty_group)
                break

    def dispatch(self, request, *args, **kwargs):
        lti = LTI(self.request_type, self.role_type)

        if hasattr(settings, 'LTI_AUTHENTICATE') and settings.LTI_AUTHENTICATE:
            # validate the user via oauth
            user = authenticate(request=request, lti=lti)
            if user is None:
                lti.clear_session(request)
                return render_to_response(
                    'lti_auth/fail_auth.html', {},
                    context_instance=RequestContext(request))

            # check if course is configured
            try:
                ctx = lti.custom_course_context()
            except (KeyError, ValueError, LTICourseContext.DoesNotExist):
                lti.clear_session(request)
                return render_to_response(
                    'lti_auth/fail_course_configuration.html', {},
                    context_instance=RequestContext(request))

            # add user to the course
            self.join_groups(lti, ctx, user)

            # login
            login(request, user)

        return super(LTIAuthMixin, self).dispatch(request, *args, **kwargs)


class LTIRoutingView(LTIAuthMixin, View):
    request_type = 'initial'
    role_type = 'any'

    def custom_landing_page(self):
        key = u'tool_consumer_info_product_family_code'
        provider = self.request.POST.get(key, '').lower()
        return 'canvas' in provider or 'blackboard' in provider

    def add_extra_parameters(self, url):
        if not hasattr(settings, 'LTI_EXTRA_PARAMETERS'):
            return

        if '?' not in url:
            url += '?'
        else:
            url += '&'

        for key in settings.LTI_EXTRA_PARAMETERS:
            value = self.request.POST.get(key, '')
            url += '{}={}&'.format(key, value)

        return url

    def post(self, request):
        if request.POST.get('ext_content_intended_use', '') == 'embed':
            domain = self.request.get_host()
            url = '%s://%s/%s?return_url=%s' % (
                self.request.scheme, domain,
                settings.LTI_TOOL_CONFIGURATION['embed_url'],
                request.POST.get('launch_presentation_return_url'))
        elif self.custom_landing_page():
            # Canvas does not support launching in a new window/tab
            # Provide a "launch in new tab" landing page
            url = reverse('lti-landing-page')
        else:
            url = '/'

        url = self.add_extra_parameters(url)

        return HttpResponseRedirect(url)


class LTIConfigView(TemplateView):
    template_name = 'lti_auth/config.xml'
    content_type = 'text/xml; charset=utf-8'

    def get_context_data(self, **kwargs):
        domain = self.request.get_host()
        launch_url = '%s://%s/%s' % (
            self.request.scheme, domain,
            settings.LTI_TOOL_CONFIGURATION['launch_url'])
        icon_url = '%s://%s/%s' % (
            self.request.scheme, domain,
            settings.LTI_TOOL_CONFIGURATION['embed_icon_url'])

        ctx = {
            'domain': domain,
            'launch_url': launch_url,
            'title': settings.LTI_TOOL_CONFIGURATION['title'],
            'description': settings.LTI_TOOL_CONFIGURATION['description'],
            'embed_icon_url': icon_url,
            'embed_tool_id': settings.LTI_TOOL_CONFIGURATION['embed_tool_id'],
            'course_navigation':
                self.request.GET.get('course_navigation', None),
            'account_navigation':
                self.request.GET.get('account_navigation', None),
            'user_navigation':
                self.request.GET.get('user_navigation', None)
        }
        return ctx


class LTILandingPage(TemplateView):
    template_name = 'lti_auth/landing_page.html'

    def get_context_data(self, **kwargs):
        domain = self.request.get_host()
        landing_url = '{}://{}/?'.format(self.request.scheme, domain)

        for key, value in self.request.GET.items():
            landing_url += '{}={}&'.format(key, value)

        return {
            'querydict': self.request.GET,
            'landing_url': landing_url,
            'title': settings.LTI_TOOL_CONFIGURATION['title']
        }


class LTICourseEnableView(View):

    @method_decorator(login_required)
    def dispatch(self, request, *args, **kwargs):
        return super(self.__class__, self).dispatch(request, *args, **kwargs)

    def post(self, *args, **kwargs):
        group_id = self.request.POST.get('group')
        faculty_group_id = self.request.POST.get('faculty_group')

        (ctx, created) = LTICourseContext.objects.get_or_create(
                group=get_object_or_404(Group, id=group_id),
                faculty_group=get_object_or_404(Group, id=faculty_group_id))

        ctx.enable = self.request.POST.get('lti-enable', 0) == '1'
        ctx.save()

        messages.add_message(self.request, messages.INFO,
                             'Your changes were saved.', fail_silently=True)

        next_url = self.request.POST.get('next', '/')
        return HttpResponseRedirect(next_url)