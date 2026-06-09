import unittest
from unittest.mock import patch

import elm_mcp_server as server


class SubscriptionHelpersTest(unittest.TestCase):
    def test_builds_ccm_oslc_user_uri(self):
        with patch.object(
            server,
            "_load_config",
            return_value={
                "host": "https://elm.example.gov.br",
                "ccm_context": "ccm",
                "jts_context": "jts",
                "username": "01464143145",
                "token": "token",
                "verify_ssl": False,
            },
        ):
            self.assertEqual(
                server._user_ccm_oslc_uri_from_identifier("01464143145"),
                "https://elm.example.gov.br/ccm/oslc/users/01464143145",
            )

    def test_parses_rdf_workitem_subscription(self):
        rdf = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:rtc_cm="http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/">
  <rdf:Description rdf:about="https://elm.example.gov.br/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/5301300">
    <dcterms:title>Validar assinatura</dcterms:title>
    <dcterms:created>2026-06-01T10:00:00.000Z</dcterms:created>
    <dcterms:modified>2026-06-08T13:00:00.000Z</dcterms:modified>
    <rtc_cm:status>Em andamento</rtc_cm:status>
    <rtc_cm:state rdf:resource="https://elm.example.gov.br/ccm/oslc/workflows/state/in_progress" />
    <rtc_cm:subscribers rdf:resource="https://elm.example.gov.br/ccm/oslc/users/01464143145" />
  </rdf:Description>
</rdf:RDF>
"""
        profile = {"id": "01464143145", "uri": "https://elm.example.gov.br/jts/users/01464143145"}

        item = server._parse_rdf_workitem_summary(rdf, profile, "5301300")

        self.assertTrue(item["subscribed"])
        self.assertEqual(item["id"], "5301300")
        self.assertEqual(item["title"], "Validar assinatura")
        self.assertEqual(item["status"], "Em andamento")
        self.assertEqual(item["subscribersCount"], 1)

    def test_parses_atom_subscription_feed_entry(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Work Item 5301300 foi alterado</title>
    <updated>2026-06-08T13:00:00.000Z</updated>
    <author><name>Analista ALM</name></author>
    <link href="https://elm.example.gov.br/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/5301300" />
    <summary>Status alterado para Em andamento</summary>
  </entry>
</feed>
"""

        events = server._parse_subscription_feed_events(feed, "", "", 10)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["workitem_id"], "5301300")
        self.assertEqual(events[0]["author"], "Analista ALM")
        self.assertIn("Status alterado", events[0]["summary"])


if __name__ == "__main__":
    unittest.main()
