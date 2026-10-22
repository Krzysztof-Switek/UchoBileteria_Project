"""Cron (hourly is enough): buyers of a cancelled event who did not choose
between the new date and a refund by the deadline get the refund."""

from django.core.management.base import BaseCommand

from apps.orders.cancellation import process_decision_deadlines


class Command(BaseCommand):
    help = "Zwrot automatyczny po terminie decyzji (odwołane wydarzenia z nowym terminem)."

    def handle(self, *args, **options):
        result = process_decision_deadlines()
        self.stdout.write(f"Zwroty: {result['refunded']}, nieudane: {result['refund_failed']}")
