from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("daily_mover_monitor", "0004_structurewatch_structureobservation")]

    operations = [
        migrations.CreateModel(
            name="DailyMoverDigest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField()),
                ("status", models.CharField(choices=[
                    ("pending", "待发送"), ("sending", "发送中"),
                    ("sent", "已发送"), ("rejected", "被拒绝"),
                    ("unknown", "发送结果待核对"),
                    ("not_configured", "未配置"),
                ], default="pending", max_length=20)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("error_summary", models.CharField(blank=True, max_length=300)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("snapshot", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="digest", to="daily_mover_monitor.dailymoversnapshot")),
            ],
        ),
    ]
