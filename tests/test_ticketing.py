import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.shared.ticketing import (
    ConfirmedPaymentReference,
    DocumentMetadataRecord,
    DocumentType,
    ElectronicTicketRecord,
    InMemoryTicketDocumentRepository,
    InvoicePaymentStatus,
    InvoiceRecord,
    IssuanceStatus,
    IssuanceStatusHistoryEntry,
    TicketCoupon,
)


class TicketDocumentRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryTicketDocumentRepository()
        self.created_at = datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc)
        self.coupon = TicketCoupon(
            coupon_id="coupon_jfk_lax",
            coupon_number=1,
            segment_id="segment_jfk_lax",
            origin_airport_code="jfk",
            destination_airport_code="lax",
            departure_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            marketing_airline_code="aa",
            flight_number="100",
            fare_basis_code="y26",
            booking_class="y",
        )

    def make_ticket(self, **overrides):
        values = {
            "ticket_id": "ticket_123",
            "ticket_number": "001-1234567890",
            "booking_id": "booking_123",
            "booking_reference": " abc123 ",
            "passenger_id": "passenger_adult",
            "validating_airline_code": "aa",
            "coupons": (self.coupon,),
            "issuance_history": (
                IssuanceStatusHistoryEntry(
                    history_id="issuance_pending",
                    status=IssuanceStatus.PENDING,
                    changed_at=self.created_at,
                    actor="ticketing-service",
                ),
            ),
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }
        values.update(overrides)
        return ElectronicTicketRecord(**values)

    def make_payment(self, **overrides):
        values = {
            "payment_intent_id": "pi_123",
            "payment_reference": "capture_123",
            "amount": Decimal("120.00"),
            "currency": "usd",
            "status": InvoicePaymentStatus.CAPTURED,
            "confirmed_at": self.created_at + timedelta(minutes=1),
        }
        values.update(overrides)
        return ConfirmedPaymentReference(**values)

    def test_ticket_number_uniqueness_is_enforced(self):
        first = self.repository.save_ticket(self.make_ticket())
        duplicate = self.make_ticket(ticket_id="ticket_456", ticket_number="0011234567890")

        self.assertEqual(first.ticket_number, "0011234567890")
        self.assertEqual(self.repository.find_ticket_by_number("001-1234567890"), first)
        with self.assertRaises(ValueError):
            self.repository.save_ticket(duplicate)

    def test_ticket_coupons_are_associated_to_flight_segments(self):
        second_coupon = TicketCoupon(
            coupon_id="coupon_lax_sfo",
            coupon_number=2,
            segment_id="segment_lax_sfo",
            origin_airport_code="LAX",
            destination_airport_code="SFO",
            departure_at=datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc),
            marketing_airline_code="AA",
            flight_number="200",
        )
        ticket = self.repository.save_ticket(self.make_ticket(coupons=(second_coupon, self.coupon)))

        self.assertEqual([coupon.segment_id for coupon in ticket.coupons], ["segment_jfk_lax", "segment_lax_sfo"])
        self.assertEqual(ticket.coupons[0].flight_designator, "AA100")
        self.assertEqual(ticket.to_dict()["coupons"][1]["flightDesignator"], "AA200")
        with self.assertRaises(ValueError):
            self.make_ticket(coupons=(self.coupon, replace(second_coupon, coupon_id="coupon_other", segment_id="segment_jfk_lax")))

    def test_invoice_is_associated_to_confirmed_payment(self):
        invoice = InvoiceRecord(
            invoice_id="invoice_123",
            invoice_number=" inv-123 ",
            booking_id="booking_123",
            booking_reference="abc123",
            payment=self.make_payment(),
            total_amount=Decimal("120"),
            currency="USD",
            issued_at=self.created_at + timedelta(minutes=2),
            line_items=(
                {"description": "Base fare", "amount": "100.00"},
                {"description": "Taxes", "amount": "20.00"},
            ),
        )

        stored = self.repository.save_invoice(invoice)

        self.assertEqual(stored.invoice_number, "INV-123")
        self.assertEqual(stored.payment.payment_intent_id, "pi_123")
        self.assertEqual(self.repository.find_invoices_by_payment_intent_id(" pi_123 "), (stored,))
        self.assertEqual(stored.to_dict()["payment"]["status"], "captured")
        with self.assertRaises(ValueError):
            InvoiceRecord(
                invoice_number="INV-124",
                booking_id="booking_123",
                booking_reference="abc123",
                payment=self.make_payment(amount=Decimal("119.99")),
                total_amount=Decimal("120.00"),
                currency="USD",
                issued_at=self.created_at,
            )

    def test_document_metadata_is_retrieved_by_booking_reference(self):
        ticket_document = DocumentMetadataRecord(
            document_id="document_ticket",
            booking_id="booking_123",
            booking_reference="abc123",
            document_type=DocumentType.E_TICKET,
            storage_uri="s3://documents/ticket.pdf",
            content_type="Application/PDF",
            related_ticket_id="ticket_123",
            checksum="sha256:abc",
            metadata={"pages": 1},
            created_at=self.created_at,
        )
        invoice_document = DocumentMetadataRecord(
            document_id="document_invoice",
            booking_id="booking_123",
            booking_reference="ABC123",
            document_type=DocumentType.INVOICE,
            storage_uri="s3://documents/invoice.pdf",
            content_type="application/pdf",
            related_invoice_id="invoice_123",
            created_at=self.created_at + timedelta(minutes=1),
        )
        other_document = DocumentMetadataRecord(
            document_id="document_other_booking",
            booking_id="booking_456",
            booking_reference="def456",
            document_type=DocumentType.ITINERARY,
            storage_uri="s3://documents/itinerary.pdf",
            content_type="application/pdf",
            created_at=self.created_at,
        )

        self.repository.save_document(ticket_document)
        self.repository.save_document(invoice_document)
        self.repository.save_document(other_document)

        documents = self.repository.find_documents_by_booking_reference(" abc123 ")
        self.assertEqual([document.document_id for document in documents], ["document_ticket", "document_invoice"])
        self.assertEqual(documents[0].content_type, "application/pdf")
        self.assertEqual(documents[0].to_dict()["metadata"], {"pages": 1})
        self.assertEqual(self.repository.find_documents_by_booking_reference("def456"), (other_document,))

    def test_issuance_status_history_is_append_only(self):
        ticket = self.repository.save_ticket(self.make_ticket())
        issued_entry = IssuanceStatusHistoryEntry(
            history_id="issuance_issued",
            status=IssuanceStatus.ISSUED,
            changed_at=self.created_at + timedelta(minutes=1),
        )

        issued = self.repository.append_issuance_status(ticket.ticket_id, issued_entry)

        self.assertEqual(issued.issuance_status, IssuanceStatus.ISSUED)
        with self.assertRaises(ValueError):
            self.repository.save_ticket(replace(issued, issuance_history=issued.issuance_history[:1]))
        with self.assertRaises(ValueError):
            self.repository.append_issuance_status(
                ticket.ticket_id,
                IssuanceStatusHistoryEntry(
                    status=IssuanceStatus.FAILED,
                    changed_at=self.created_at - timedelta(seconds=1),
                ),
            )


if __name__ == "__main__":
    unittest.main()
