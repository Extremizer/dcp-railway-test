import unittest
from types import SimpleNamespace

from customs_completeness import REQUIRED_CUSTOMS_FIELDS, validate_customs_profile


class CustomsCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.profile = dict(description_ru="Масляный фильтр", description_en="Oil Filter",
                            manufacturer="Ski-Doo", country_of_origin="Canada",
                            gross_weight_kg=0.10, net_weight_kg=0.08,
                            equipment_type="SNOWMOBILE")

    def test_complete_mapping_and_object(self):
        for profile in (self.profile, SimpleNamespace(**self.profile)):
            result = validate_customs_profile(profile)
            self.assertTrue(result.is_complete)
            self.assertEqual(result.missing_fields, ())

    def test_incomplete_example(self):
        self.profile.update(country_of_origin=None, gross_weight_kg=None)
        result = validate_customs_profile(self.profile)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.missing_fields, ("country_of_origin", "gross_weight_kg"))

    def test_each_required_field_missing_none_and_blank(self):
        for field in REQUIRED_CUSTOMS_FIELDS:
            for value in (None, "", " \t\u00a0 "):
                with self.subTest(field=field, value=value):
                    profile = dict(self.profile, **{field: value})
                    self.assertEqual(validate_customs_profile(profile).missing_fields, (field,))
            profile = self.profile.copy()
            del profile[field]
            self.assertEqual(validate_customs_profile(profile).missing_fields, (field,))

    def test_only_presence_no_value_verification(self):
        self.profile.update(gross_weight_kg=0, net_weight_kg=-1,
                            equipment_type="unverified", country_of_origin="unverified")
        self.assertTrue(validate_customs_profile(self.profile).is_complete)

    def test_no_shipment_price_quantity_or_oem_requirements(self):
        self.assertTrue(validate_customs_profile(self.profile).is_complete)
        self.assertEqual(validate_customs_profile({}).missing_fields, REQUIRED_CUSTOMS_FIELDS)
        self.assertEqual(len(REQUIRED_CUSTOMS_FIELDS), 7)

    def test_does_not_mutate_profile(self):
        before = self.profile.copy()
        validate_customs_profile(self.profile)
        self.assertEqual(self.profile, before)


if __name__ == "__main__":
    unittest.main()
