from .models import Profile

ADMIN_ROLES = {Profile.Role.ADMIN, Profile.Role.MANAGER}


def profile_for(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def is_admin_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    try:
        return user.profile.role in ADMIN_ROLES
    except Profile.DoesNotExist:
        return False


def has_totp_configured(user):
    if not is_admin_user(user):
        return False
    profile = profile_for(user)
    return profile.totp_enabled and bool(profile.get_totp_secret())


def needs_totp_setup(user):
    return is_admin_user(user) and not has_totp_configured(user)


def can_use_django_admin(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.is_superuser
        and has_totp_configured(user)
    )
