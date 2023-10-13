from rest_framework import permissions, response
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from ..models import Company
from .serializers import CompanyConnectSerializer, CompanySerializer


class CompanyViewSet(
    RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = CompanySerializer
    queryset = Company.objects.all()
    lookup_field = "slug"

    def get_object(self):
        """
        Handles special case of '0' or 0 or '_' meaning current user
        :return: requested user details
        """
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        if self.kwargs[lookup_url_kwarg] in (0, "0", "_"):
            return self.get_queryset().first()
        return super().get_object()

    @action(methods=["POST"], detail=True, serializer_class=CompanyConnectSerializer)
    def connect(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return response.Response(serializer.data)
