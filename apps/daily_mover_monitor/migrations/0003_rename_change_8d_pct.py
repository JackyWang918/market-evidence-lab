from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("daily_mover_monitor", "0002_dailymoverpreviewreport")]

    operations = [
        migrations.RenameField(
            model_name="dailymoverentry",
            old_name="change_8d_pct",
            new_name="baseline_change_pct",
        ),
    ]
