import unittest

from bulk_order_parser import parse_bulk_order


class BulkOrderParserTests(unittest.TestCase):
    def test_quantity_formats(self):
        for suffix in (" 2", " x2", " x 2", " ×2", " *2", " - 2",
                       " - 2 шт", ";2", ",2", "\t2"):
            with self.subTest(suffix=suffix):
                result = parse_bulk_order("417300574" + suffix)
                row, = result.items
                self.assertEqual((row.raw_oem, row.normalized_oem, row.quantity,
                                  row.status), ("417300574", "417300574", 2, "PARSED"))
                self.assertEqual(result.status, "READY")

    def test_canonical_five_lines(self):
        result = parse_bulk_order("417300574 x2\n860200987 - 1 шт\n"
                                  "705402449    4\n0627-111;3\n3006-682")
        self.assertEqual([(r.normalized_oem, r.quantity, r.status) for r in result.items],
                         [("417300574", 2, "PARSED"), ("860200987", 1, "PARSED"),
                          ("705402449", 4, "PARSED"), ("0627-111", 3, "PARSED"),
                          ("3006-682", 1, "PARSED")])
        self.assertEqual((result.total_lines, result.parsed_count), (5, 5))

    def test_leading_zero_and_surrounding_whitespace(self):
        for text in ("0746-435", "\u200b\u00a00746-435\u00a0\ufeff", " 0746-435 "):
            with self.subTest(text=text):
                row, = parse_bulk_order(text).items
                self.assertEqual((row.raw_oem, row.normalized_oem, row.quantity),
                                 ("0746-435", "0746-435", 1))
                self.assertEqual(row.raw_text, text)

    def test_numbered_and_bullet_lists(self):
        for prefixes in (("1. ", "2. "), ("1) ", "2) "), ("- ", "- "), ("• ", "• ")):
            with self.subTest(prefixes=prefixes):
                result = parse_bulk_order(prefixes[0] + "417300574 x2\n" +
                                          prefixes[1] + "860200987 x1")
                self.assertEqual([r.quantity for r in result.items], [2, 1])
                self.assertEqual(result.status, "READY")

    def test_table_headers(self):
        for oem in ("OEM", "Part Number", "Part No", "P/N", "Артикул",
                    "Каталожный номер", "Номер"):
            for qty in ("Qty", "Quantity", "Кол-во", "Количество", "Шт"):
                with self.subTest(oem=oem, qty=qty):
                    result = parse_bulk_order(f"{oem}\t{qty}\n417300574\t2\n860200987\t1")
                    self.assertEqual([r.source_line_number for r in result.items], [2, 3])
                    self.assertEqual([r.quantity for r in result.items], [2, 1])
                    self.assertEqual(result.total_lines, 2)

    def test_reversed_table_columns(self):
        result = parse_bulk_order("Qty;OEM\n2;0746-435")
        self.assertEqual((result.items[0].normalized_oem, result.items[0].quantity),
                         ("0746-435", 2))

    def test_ambiguous_quantity(self):
        row, = parse_bulk_order("417300574 20 35").items
        self.assertEqual((row.status, row.error_code, row.quantity),
                         ("NEEDS_REVIEW", "AMBIGUOUS_QTY", None))

    def test_multiple_oem_not_split(self):
        row, = parse_bulk_order("417300574 / 860200987 x2").items
        self.assertEqual((row.status, row.error_code, row.quantity),
                         ("NEEDS_REVIEW", "MULTIPLE_OEM", None))

    def test_missing_oem(self):
        for text in ("нужен ремень на Ski-Doo", ";2", "\t2"):
            with self.subTest(text=text):
                row, = parse_bulk_order(text).items
                self.assertEqual((row.status, row.error_code), ("INVALID", "MISSING_OEM"))

    def test_invalid_quantity(self):
        for qty in ("0", "-1", "1.5"):
            with self.subTest(qty=qty):
                row, = parse_bulk_order("417300574 x" + qty).items
                self.assertEqual((row.status, row.error_code, row.quantity),
                                 ("INVALID", "INVALID_QTY", None))

    def test_partial_success(self):
        result = parse_bulk_order("417300574 x2\nнужен ремень на Ski-Doo\n860200987 x1")
        self.assertEqual([r.status for r in result.items], ["PARSED", "INVALID", "PARSED"])
        self.assertEqual((result.parsed_count, result.review_count, result.invalid_count,
                          result.status), (2, 0, 1, "NEEDS_REVIEW"))

    def test_duplicates_without_merge(self):
        result = parse_bulk_order("417300574 x2\n417300574 x3\n0746-435")
        self.assertEqual(len(result.items), 3)
        first, second, third = result.items
        self.assertEqual([first.quantity, second.quantity], [2, 3])
        self.assertEqual(first.duplicate_group_id, second.duplicate_group_id)
        self.assertIsNotNone(first.duplicate_group_id)
        for row in (first, second):
            self.assertEqual((row.status, row.error_code, row.suggested_total_quantity),
                             ("DUPLICATE", "DUPLICATE_POSSIBLE", 5))
        self.assertEqual(result.duplicate_groups[first.duplicate_group_id], (1, 2))
        self.assertEqual((result.parsed_count, result.review_count, result.status),
                         (1, 2, "NEEDS_REVIEW"))
        self.assertEqual(third.status, "PARSED")
        self.assertEqual(result, parse_bulk_order("417300574 x2\n417300574 x3\n0746-435"))

    def test_empty_and_blank_lines(self):
        for text in ("", " \n\t\n\u00a0", "OEM\tQty"):
            result = parse_bulk_order(text)
            self.assertEqual((result.items, result.total_lines, result.status), ((), 0, "EMPTY"))
        result = parse_bulk_order("\n417300574\n\n860200987\n")
        self.assertEqual([r.source_line_number for r in result.items], [2, 4])
        self.assertEqual(result.invalid_count, 0)


if __name__ == "__main__":
    unittest.main()
