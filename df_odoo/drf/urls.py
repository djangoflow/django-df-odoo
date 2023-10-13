from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

from .viewsets import CompanyViewSet

urlpatterns = []

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()

router.register("companies", CompanyViewSet, basename="companies")

urlpatterns += router.urls
