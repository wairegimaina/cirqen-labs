from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import UserProfile, UserSignature

User = get_user_model()

@receiver(post_save, sender=User)
def create_user_related_objects(sender, instance, created, **kwargs):
    """
    Automatically create UserProfile and UserSignature when a User is created,
    and save them on User update.
    """
    if created:
        UserProfile.objects.create(user=instance)
        UserSignature.objects.create(user=instance)
    else:
        # Update related objects if they exist
        if hasattr(instance, 'userprofile'):
            instance.userprofile.save()
        if hasattr(instance, 'signature'):
            instance.signature.save()
