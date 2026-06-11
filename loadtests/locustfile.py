"""
Load tests (spec section 25.5). Run against a staging instance in DEMO mode:

    pip install locust
    locust -f loadtests/locustfile.py --host https://staging.example.com \
           -u 50 -r 10 --run-time 2m

Set EVENT_SLUG to a published demo event with limited capacity to also
exercise the "fight for the last ticket" path.
"""

import os
import random

from locust import HttpUser, between, task

EVENT_SLUG = os.environ.get("EVENT_SLUG", "test-koncert")


class Buyer(HttpUser):
    wait_time = between(0.5, 3)

    @task(10)
    def browse_event_page(self):
        self.client.get(f"/wydarzenia/{EVENT_SLUG}/", name="event page")

    @task(2)
    def browse_list(self):
        self.client.get("/", name="event list")

    @task(1)
    def buy_ticket(self):
        page = self.client.get(f"/wydarzenia/{EVENT_SLUG}/", name="event page")
        token = None
        for line in page.text.splitlines():
            if "csrfmiddlewaretoken" in line:
                token = line.split('value="')[1].split('"')[0]
                break
        if not token:
            return
        self.client.post(
            f"/kup/{EVENT_SLUG}/",
            {
                "buyer_email": f"load{random.randint(1, 99999)}@example.com",
                "quantity": 1,
                "csrfmiddlewaretoken": token,
            },
            headers={"Referer": f"{self.host}/wydarzenia/{EVENT_SLUG}/"},
            name="purchase",
        )
