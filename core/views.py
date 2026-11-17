import sys
import django
from django.shortcuts import render
from django.utils import timezone
from django.conf import settings

def home(request):
    """
    FactoryFlow Home & Health Check View.
    Displays project overview, technical environment info, and phase status.
    """
    db_engine = settings.DATABASES['default']['ENGINE'].split('.')[-1]
    context = {
        'project_name': 'FactoryFlow',
        'subtitle': 'Smart Order-to-Audit Management System',
        'organisation': 'NIT Tiruchirappalli - Winter Internship 2026',
        'python_version': sys.version.split(' ')[0],
        'django_version': django.get_version(),
        'db_engine': db_engine,
        'current_time': timezone.now(),
        'timezone': settings.TIME_ZONE,
        'debug_mode': settings.DEBUG,
        'phase': 1,
        'phase_title': 'Phase 1: Project Setup & Django Environment Configuration',
    }
    return render(request, 'core/home.html', context)
