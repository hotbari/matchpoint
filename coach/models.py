from django.db import models
from club.models import Club
from users.models import CustomUser
from core.models import TimeStampedModel, SoftDeleteModel

class Coach(TimeStampedModel, SoftDeleteModel):
    club = models.ForeignKey(Club, models.DO_NOTHING, related_name='coaches')
    user = models.ForeignKey(CustomUser, models.DO_NOTHING)

    def __int__(self):
        return self.id

    class Meta:
        db_table = 'coach'