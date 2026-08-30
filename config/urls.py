from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Provider webhooks: /webhooks/messaging/<provider>/
    path("webhooks/messaging/", include("messaging.urls")),
    path("", include("core.urls")),
]

# Uploaded template media, dev only (static() is a no-op when DEBUG is off).
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
