from django.test import TestCase, Client
from django.urls import reverse
from django.db import connection

class Phase1SetupTests(TestCase):
    """
    Automated verification tests for Phase 1:
    - Environment sanity
    - Database connection
    - Home view routing and template rendering
    """

    def setUp(self):
        self.client = Client()

    def test_database_connection(self):
        """Verify that SQLite/PostgreSQL connection is healthy."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            row = cursor.fetchone()
            self.assertEqual(row[0], 1)

    def test_home_page_status_code(self):
        """Verify that home page responds with HTTP 200 OK."""
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_home_page_content(self):
        """Verify that home page contains FactoryFlow branding and Phase 1 checklist."""
        response = self.client.get(reverse('home'))
        self.assertContains(response, "FactoryFlow")
        self.assertContains(response, "Smart Order-to-Audit Management System")
        self.assertContains(response, "NIT Tiruchirappalli")
        self.assertTemplateUsed(response, 'core/home.html')
        self.assertTemplateUsed(response, 'base.html')
