# users/backends.py
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class HybridAuthBackend(ModelBackend):
    """
    Authenticate against default database only.
    Simplified version that uses Django's built-in ModelBackend.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        """Authenticate user against default database"""
        if not username or not password:
            return None

        try:
            # Authenticate against default database only
            user = User.objects.using('default').get(username=username)

            # Check if user is active
            if not user.active_status:
                logger.warning(f"Inactive user attempted login: {username}")
                return None

            # Check password
            if user.check_password(password):
                logger.info(f"Authentication successful for user: {username}")
                return user
            else:
                logger.warning(f"Invalid password for user: {username}")
                return None

        except User.DoesNotExist:
            logger.debug(f"User {username} not found in database")
            return None
        except Exception as e:
            logger.error(f"Authentication error for {username}: {e}")
            return None

    def get_user(self, user_id):
        """Get user by ID from default database"""
        try:
            user = User.objects.using('default').get(pk=user_id)
            # Only return user if active
            if user.active_status:
                return user
            return None
        except User.DoesNotExist:
            logger.debug(f"User with ID {user_id} not found")
            return None
        except Exception as e:
            logger.error(f"Error retrieving user {user_id}: {e}")
            return None
