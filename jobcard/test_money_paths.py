"""Stock and approval: the paths that move money and stock."""
from decimal import Decimal

from django.core.exceptions import ValidationError

from jobcard.models import SparePartUsed
from jobcard import tests as flow  # module import: its test class is not collected twice
from parts_tools.models import Accessories, Accessoriesname


class MoneyPathTests(flow.JobCardFlowTests):
    """Reuses JobCardFlowTests' fixtures; its own tests are not re-run here."""

    def _card_with_part(self, stock=5, quantity=2, cost=Decimal("150.00")):
        self.part = Accessories.objects.create(name=Accessoriesname.objects.create(name="Sensor"),
                                               equipment_description=self.equipment.description,
                                               workshop=self.workshop, stock_count=stock, unit_cost=cost)
        card = self.raise_job_card()
        SparePartUsed.objects.create(job_card=card, part=self.part, quantity=quantity, unit_cost=cost)
        return card

    def test_approval_deducts_stock_once_and_costs_the_parts(self):
        card = self._card_with_part()
        card.approve_job_card(self.nic, nurse_name="Nurse In-charge")
        self.part.refresh_from_db()
        card.refresh_from_db()
        self.assertEqual(self.part.stock_count, 3)
        self.assertEqual((card.status, card.stock_deducted), ("Approved", True))
        self.assertEqual(str(card.total_parts_cost), "300.00")
        card.deduct_stock()  # already deducted: no second deduction
        self.part.refresh_from_db()
        self.assertEqual(self.part.stock_count, 3)
        with self.assertRaises(ValidationError):
            card.approve_job_card(self.nic)

    def test_a_part_cannot_be_added_beyond_stock(self):
        card = self._card_with_part(stock=5, quantity=2)
        with self.assertRaisesRegex(ValidationError, "Insufficient stock"):
            SparePartUsed.objects.create(job_card=card, part=self.part, quantity=9, unit_cost=Decimal("1.00"))

    def test_stock_used_elsewhere_meanwhile_refuses_approval(self):
        card = self._card_with_part(stock=5, quantity=2)
        Accessories.objects.filter(pk=self.part.pk).update(stock_count=1)  # another card took it
        with self.assertRaisesRegex(ValidationError, "Insufficient stock"):
            card.approve_job_card(self.nic)
        self.part.refresh_from_db()
        self.assertEqual(self.part.stock_count, 1)

    def test_declining_an_approved_card_restores_stock(self):
        card = self._card_with_part()
        card.approve_job_card(self.nic)
        card.decline_job_card(self.nic, "Wrong machine", nurse_name="Nurse In-charge")
        self.part.refresh_from_db()
        card.refresh_from_db()
        self.assertEqual(self.part.stock_count, 5)
        self.assertEqual((card.status, card.decline_reason, card.stock_deducted), ("Declined", "Wrong machine", False))
        with self.assertRaises(ValidationError):
            card.decline_job_card(self.nic, "again")
        with self.assertRaises(ValidationError):
            card.approve_job_card(self.nic)
        card.restore_stock()  # nothing deducted: no change
        self.part.refresh_from_db()
        self.assertEqual(self.part.stock_count, 5)


# Only the tests above belong to this module.
for _name in [n for n in dir(flow.JobCardFlowTests) if n.startswith("test_")]:
    if _name not in MoneyPathTests.__dict__:
        setattr(MoneyPathTests, _name, None)
