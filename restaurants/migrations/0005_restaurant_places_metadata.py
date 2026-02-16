from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("restaurants", "0004_enforce_session_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="business_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="place_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="price_level",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="rating",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="types",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="user_ratings_total",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
