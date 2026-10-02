import mimetypes
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .catalog_order import interleave_template_cards
from .forms import TestimonialSubmissionForm
from .frombiz_raw_templates import (
    has_raw_frombiz_template,
    raw_frombiz_asset_path,
    read_raw_frombiz_index,
    read_raw_frombiz_palette_css,
)
from .models import Service, TemplateProduct, Testimonial
from .presentation import (
    clean_service,
    clean_template_product,
    client_safe_category_filters,
)


def services(request):
    items = [clean_service(service) for service in Service.objects.filter(is_published=True)]
    return render(request, "content/services.html", {"services": items})


def service_detail(request, slug):
    service = get_object_or_404(Service, slug=slug, is_published=True)
    return render(request, "content/service_detail.html", {"service": clean_service(service)})


def reviews(request):
    items = Testimonial.objects.filter(is_published=True)
    form = None
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=reverse("accounts:login"))

        form = TestimonialSubmissionForm(request.POST)
        recent_submissions = Testimonial.objects.filter(
            submitted_by=request.user,
            created_at__gte=timezone.now() - timedelta(days=1),
        ).count()
        if recent_submissions >= 3:
            form.add_error(None, "За сутки можно отправить не больше трех отзывов.")
        elif form.is_valid():
            testimonial = form.save(commit=False)
            testimonial.submitted_by = request.user
            testimonial.publication_consent_at = timezone.now()
            testimonial.is_published = False
            testimonial.save()
            messages.success(request, "Спасибо! Отзыв отправлен и появится на сайте после проверки.")
            return redirect("content:reviews")
    elif request.user.is_authenticated:
        try:
            client_details = request.user.profile.company
        except AttributeError:
            client_details = ""
        form = TestimonialSubmissionForm(
            initial={
                "client_name": request.user.get_full_name() or request.user.username,
                "client_details": client_details,
            }
        )

    return render(
        request,
        "content/reviews.html",
        {
            "testimonials": items,
            "review_form": form,
        },
    )


def templates(request):
    items = TemplateProduct.objects.filter(
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    )
    category = request.GET.get("category", "").strip()
    if category:
        items = items.filter(category=category)
    template_count = items.count()
    category_values = (
        TemplateProduct.objects.filter(
            is_published=True,
            template_type=TemplateProduct.TemplateType.WEBSITE,
        )
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )
    template_cards = [
        {
            "template": clean_template_product(template),
            "preview_desktop_webp": f"template-previews/desktop-webp/{template.slug}.webp",
            "preview_mobile_webp": f"template-previews/mobile-webp/{template.slug}.webp",
        }
        for template in items
    ]
    if not category:
        template_cards = interleave_template_cards(template_cards)
    return render(
        request,
        "content/templates.html",
        {
            "templates": template_cards,
            "template_count": template_count,
            "categories": client_safe_category_filters(category_values),
            "selected_category": category,
        },
    )


def template_demo(request, slug):
    get_object_or_404(
        TemplateProduct,
        slug=slug,
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    )
    return redirect("template-site-demo", slug=slug)


def template_site_demo(request, slug):
    template = get_object_or_404(
        TemplateProduct,
        slug=slug,
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    )
    if has_raw_frombiz_template(template.slug):
        body = read_raw_frombiz_index(template.slug)
    else:
        raise Http404("Standalone template source is unavailable.")
    response = HttpResponse(body, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-same-origin; "
        "default-src 'self' data: blob:; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'none'; "
        "media-src 'self' data:; "
        "frame-src 'self'; "
        "object-src 'none'; "
        "form-action 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'self'"
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Referrer-Policy"] = "no-referrer"
    response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    return response


def template_site_asset(request, slug, path):
    template = get_object_or_404(
        TemplateProduct,
        slug=slug,
        is_published=True,
        template_type=TemplateProduct.TemplateType.WEBSITE,
    )
    if has_raw_frombiz_template(template.slug):
        try:
            raw_asset_path = raw_frombiz_asset_path(template.slug, path)
        except SuspiciousFileOperation as exc:
            raise Http404("Asset not found") from exc
        if raw_asset_path is not None:
            content_type, _encoding = mimetypes.guess_type(str(raw_asset_path))
            if path == "aurum-palette.css":
                response = HttpResponse(
                    read_raw_frombiz_palette_css(template.slug),
                    content_type="text/css; charset=utf-8",
                )
            else:
                response = FileResponse(
                    raw_asset_path.open("rb"), content_type=content_type or "application/octet-stream"
                )
            response["Cache-Control"] = "public, max-age=3600"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

    raise Http404("Asset not found")
