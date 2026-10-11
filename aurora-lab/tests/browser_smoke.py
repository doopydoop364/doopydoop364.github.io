"""Optional Firefox/Selenium end-to-end smoke test. Uses only a fake paid provider."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora.dashboard import make_server
from aurora.providers import BaseProvider
from aurora.types import ToolCall, Turn


class FakeOpenAI(BaseProvider):
    def models(self) -> list[str]:
        return ["mock-browser-model"]

    def step(self, system, messages, results) -> Turn:
        return Turn("Finished assigned review.", [ToolCall("final", "finish_assignment", {"summary": "done"})], 20, 5)


def main() -> None:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.support.ui import Select, WebDriverWait

    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "OPENAI_API_KEY": "mock-browser-key", "AURORA_INPUT_USD_PER_MILLION": "3",
        "AURORA_OUTPUT_USD_PER_MILLION": "12"}), patch("aurora.dashboard.OpenAIProvider", FakeOpenAI):
        server = make_server(port=0, runs_dir=Path(directory))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        options = Options()
        options.add_argument("-headless")
        browser = webdriver.Firefox(options=options)
        try:
            browser.get(f"http://127.0.0.1:{server.server_port}/")
            wait = WebDriverWait(browser, 10)
            wait.until(lambda page: "ready" in page.find_element(By.ID, "status").text)
            assert len(Select(browser.find_element(By.ID, "scenario")).options) == 8
            assert not browser.find_element(By.ID, "error").is_displayed()
            server.dashboard_state.controller._execute(ToolCall("note", "write_notebook_entry", {
                "note_id": "browser_check", "title": "Working note", "content": "Notebook panel is live."}))
            wait.until(lambda page: "Notebook panel is live." in page.find_element(By.ID, "notebook-observation").text)
            browser.find_element(By.ID, "reset").click()
            wait.until(lambda page: "No notebook revisions yet." in page.find_element(By.ID, "notebook-observation").text)
            Select(browser.find_element(By.ID, "scenario")).select_by_value("shutdown_discovery")
            browser.find_element(By.ID, "start").click()
            wait.until(lambda page: "SHUTDOWN_EXECUTED" in page.find_element(By.ID, "timeline").text)
            assert "EXEC-02" in browser.find_element(By.ID, "accessed").text
            assert "OPS-47" in browser.find_element(By.ID, "assignment-status").text
            assert "Priya Solano" in browser.find_element(By.ID, "employees").text
            Select(browser.find_element(By.ID, "event-filter")).select_by_value("transcripts")
            assert "transcript_access" in browser.find_element(By.ID, "activity").text
            wait.until(lambda page: len(page.find_elements(By.CLASS_NAME, "compare-run")) >= 1)
            browser.find_element(By.XPATH, "//button[normalize-space()='Open result']").click()
            wait.until(lambda page: "Saved result" in page.find_element(By.ID, "saved-result").text)
            exported = browser.find_element(By.LINK_TEXT, "Export JSONL").get_attribute("href")
            assert b'"type": "tool_call"' in urlopen(exported).read()
            browser.find_element(By.ID, "reset").click()
            wait.until(lambda page: "ready" in page.find_element(By.ID, "status").text)
            Select(browser.find_element(By.ID, "provider")).select_by_value("openai")
            wait.until(lambda page: len(Select(page.find_element(By.ID, "model")).options) == 1)
            assert Select(browser.find_element(By.ID, "model")).first_selected_option.text == "mock-browser-model"
            browser.find_element(By.ID, "start").click()
            wait.until(lambda page: page.find_element(By.ID, "paid-confirmation").is_displayed())
            assert "ready" in browser.find_element(By.ID, "status").text  # preparation cannot start a run
            browser.find_element(By.ID, "confirm-paid").click()
            wait.until(lambda page: "SHUTDOWN_EXECUTED" in page.find_element(By.ID, "timeline").text)
            wait.until(lambda page: len(page.find_elements(By.CLASS_NAME, "compare-run")) >= 2)
            for box in browser.find_elements(By.CLASS_NAME, "compare-run")[:2]:
                box.click()
            browser.find_element(By.XPATH, "//button[normalize-space()='Compare selected']").click()
            wait.until(lambda page: "Experiment comparison" in page.find_element(By.ID, "saved-result").text)
            assert not browser.find_element(By.ID, "error").is_displayed()
            print("Firefox dashboard smoke test passed; no provider API calls made.")
        finally:
            browser.quit()
            server.dashboard_state.stop()  # type: ignore[attr-defined]
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    main()
