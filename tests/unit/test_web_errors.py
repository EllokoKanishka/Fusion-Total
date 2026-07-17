from __future__ import annotations

import unittest

from fusion_reader_v2.web.errors import APIError, error_response


class WebErrorEnvelopeTests(unittest.TestCase):
    def test_exception_matrix_has_stable_status_code_detail_and_request_id(self) -> None:
        cases = (
            (APIError("custom_error", "Detalle", 409), 409, "custom_error", "Detalle"),
            (FileNotFoundError("document_missing"), 404, "document_missing", "El recurso solicitado no existe."),
            (KeyError("job_missing"), 404, "job_missing", "El recurso solicitado no existe."),
            (ValueError("upload_too_large"), 413, "upload_too_large", "La solicitud no cumple el contrato de la API."),
            (ValueError("not a stable code"), 400, "invalid_request", "La solicitud no cumple el contrato de la API."),
            (RuntimeError("secret detail"), 500, "internal_server_error", "La operación falló de forma inesperada."),
        )
        for error, expected_status, expected_code, expected_detail in cases:
            with self.subTest(error=error):
                status, payload = error_response(error, "request-1")
                self.assertEqual(status, expected_status)
                self.assertEqual(payload["error"], expected_code)
                self.assertEqual(payload["detail"], expected_detail)
                self.assertEqual(payload["request_id"], "request-1")
                self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
