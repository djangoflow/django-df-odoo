from hashid_field.rest import HashidSerializerCharField
from rest_framework import serializers

from ..models import Company


class CompanyBaseSerializer(serializers.ModelSerializer):
    url = serializers.URLField(required=False, read_only=True)
    slug = HashidSerializerCharField(read_only=True)


class CompanySerializer(CompanyBaseSerializer):
    id = HashidSerializerCharField(read_only=True)

    class Meta:
        model = Company
        read_only_fields = (
            "id",
            "url",
            "slug",
        )
        fields = read_only_fields


class CompanyConnectSerializer(CompanyBaseSerializer):
    redirect = serializers.CharField(default="/")
    url = serializers.CharField(read_only=True)

    def save(self, **kwargs):
        self.instance.url = self.instance.login_user(
            self.context["request"].user, redirect=self.validated_data["redirect"]
        )
        return self.instance

    class Meta:
        model = Company
        read_only_fields = ("website_url", "url", "name", "slug", "city", "street")
        fields = read_only_fields + ("redirect",)
