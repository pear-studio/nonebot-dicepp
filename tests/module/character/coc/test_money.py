import unittest
import pytest
from module.character.coc.money import MoneyInfo


@pytest.mark.unit
class TestCocMoneyInfo(unittest.TestCase):
    def test_init(self):
        money = MoneyInfo()
        self.assertEqual(money.gold, 0)
        self.assertEqual(money.silver, 0)
        self.assertEqual(money.copper, 0)

    def test_set_values(self):
        money = MoneyInfo()
        money.gold = 100
        money.silver = 50
        money.copper = 25
        self.assertEqual(money.gold, 100)
        self.assertEqual(money.silver, 50)
        self.assertEqual(money.copper, 25)

    def test_serialization(self):
        money = MoneyInfo()
        money.gold = 100
        money.silver = 50
        money.copper = 25
        serialized = money.serialize()
        self.assertIn("100", serialized)
        self.assertIn("50", serialized)
        self.assertIn("25", serialized)

    def test_deserialization(self):
        money = MoneyInfo()
        money.gold = 100
        money.silver = 50
        money.copper = 25
        serialized = money.serialize()

        money2 = MoneyInfo()
        money2.deserialize(serialized)
        self.assertEqual(money2.gold, 100)
        self.assertEqual(money2.silver, 50)
        self.assertEqual(money2.copper, 25)

    def test_roundtrip(self):
        money = MoneyInfo()
        money.gold = 50
        money.silver = 30
        money.copper = 10

        serialized = money.serialize()
        money2 = MoneyInfo()
        money2.deserialize(serialized)

        self.assertEqual(money.gold, money2.gold)
        self.assertEqual(money.silver, money2.silver)
        self.assertEqual(money.copper, money2.copper)


if __name__ == '__main__':
    unittest.main()
