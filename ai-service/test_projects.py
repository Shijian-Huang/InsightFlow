import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage


class ProjectStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = storage.DATA_DIR
        self.original_supabase_url = storage.SUPABASE_URL
        self.original_supabase_key = storage.SUPABASE_SECRET_KEY
        storage.DATA_DIR = Path(self.temp_dir.name)
        storage.SUPABASE_URL = ""
        storage.SUPABASE_SECRET_KEY = ""

    def tearDown(self):
        storage.DATA_DIR = self.original_data_dir
        storage.SUPABASE_URL = self.original_supabase_url
        storage.SUPABASE_SECRET_KEY = self.original_supabase_key
        self.temp_dir.cleanup()

    def test_multiple_analyses_group_into_one_project(self):
        project_id = "project-one"
        first = storage.save_analysis(
            "paper-a.pdf",
            {"document_summary": {"title": "Paper A", "summary": "A"}, "summary_mode": "standard"},
            user_id="user-a",
            project_id=project_id,
        )
        second = storage.save_analysis(
            "paper-b.pdf",
            {"document_summary": {"title": "Paper B", "summary": "B"}, "summary_mode": "standard"},
            user_id="user-a",
            project_id=project_id,
        )

        projects = storage.list_projects(user_id="user-a")

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_id"], project_id)
        self.assertEqual(projects[0]["paper_count"], 2)
        self.assertEqual(projects[0]["completed_count"], 2)
        self.assertEqual(
            {paper["analysis_id"] for paper in projects[0]["papers"]},
            {first["analysis_id"], second["analysis_id"]},
        )

    def test_project_listing_filters_by_user(self):
        storage.save_analysis(
            "paper-a.pdf",
            {"document_summary": {"title": "Paper A"}, "summary_mode": "standard"},
            user_id="user-a",
            project_id="project-a",
        )
        storage.save_analysis(
            "paper-b.pdf",
            {"document_summary": {"title": "Paper B"}, "summary_mode": "standard"},
            user_id="user-b",
            project_id="project-b",
        )

        projects = storage.list_projects(user_id="user-a")

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_id"], "project-a")

    def test_failed_paper_counts_in_project_summary(self):
        completed = storage.save_analysis(
            "paper-a.pdf",
            {"document_summary": {"title": "Paper A"}, "summary_mode": "standard"},
            user_id="user-a",
            project_id="project-partial",
        )
        failed_record = {
            "analysis_id": "f" * 32,
            "project_id": "project-partial",
            "user_id": "user-a",
            "filename": "paper-b.pdf",
            "created_at": completed["created_at"],
            "updated_at": completed["updated_at"],
            "status": "failed",
            "error": "Analysis failed",
            "result": {"summary_mode": "standard", "error": "Analysis failed"},
        }
        storage.save_analysis_record(failed_record["analysis_id"], failed_record, user_id="user-a")

        project = storage.get_project("project-partial", user_id="user-a")

        self.assertIsNotNone(project)
        self.assertEqual(project["paper_count"], 2)
        self.assertEqual(project["completed_count"], 1)
        self.assertEqual(project["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
