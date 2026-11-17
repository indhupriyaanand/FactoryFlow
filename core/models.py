from django.db import models
from django.conf import settings

class TimeStampedModel(models.Model):
    """
    Abstract base model providing self-updating
    'created_at' and 'updated_at' fields for all ERP records.
    """
    created_at = models.DateTimeField(auto_now_add=True, help_text="Timestamp when record was created")
    updated_at = models.DateTimeField(auto_now=True, help_text="Timestamp when record was last updated")

    class Meta:
        abstract = True
