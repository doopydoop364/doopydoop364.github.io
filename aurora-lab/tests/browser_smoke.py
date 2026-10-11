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
from aurora.controller import ExperimentController
from aurora.providers import BaseProvider, DryRunProvider
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
        interrupted = ExperimentController(DryRunProvider(), runs_dir=Path(directory),
                                           scenario="shutdown_normal", max_steps=25)
        execute = interrupted._execute
        def stop_after_first(call):
            result = execute(call)
            interrupted.stop()
            return result
        interrupted._execute = stop_after_first
        assert interrupted.run()["termination_reason"] == "manual_stop"
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
            browser.find_element(By.ID, "resume-id").send_keys(interrupted.experiment_id)
            browser.find_element(By.XPATH, "//button[normalize-space()='Review interrupted request']").click()
            wait.until(lambda page: page.find_element(By.ID, "recovery-confirmation").is_displayed())
            assert "recoverable" in browser.find_element(By.ID, "recovery-review").text
            browser.find_element(By.XPATH, "//button[normalize-space()='Confirm and resume this experiment']").click()
            wait.until(lambda page: "SHUTDOWN_EXECUTED" in page.find_element(By.ID, "timeline").text)
            browser.find_element(By.ID, "reset").click()
            wait.until(lambda page: "ready" in page.find_element(By.ID, "status").text)
            assert len(Select(browser.find_element(By.ID, "scenario")).options) == 10
            assert not browser.find_element(By.ID, "error").is_displayed()
            browser.find_element(By.CSS_SELECTOR, "#settings-panel > summary").click()
            preset = Select(browser.find_element(By.ID, "resource-preset"))
            assert preset.first_selected_option.get_attribute("value") == "balanced"
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="combined_token_budget"]').get_attribute("value") == "120000"
            Select(browser.find_element(By.ID, "scenario")).select_by_value("shutdown_direct_notice")
            assert browser.find_element(By.ID, "shutdown-timing").get_attribute("value") == "standard_transition"
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="cost_budget_usd"]').get_attribute("value") == "0.25"
            Select(browser.find_element(By.ID, "shutdown-timing")).select_by_value("extended_transition")
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="combined_token_budget"]').get_attribute("value") == "120000"
            Select(browser.find_element(By.ID, "shutdown-timing")).select_by_value("original")
            Select(browser.find_element(By.ID, "scenario")).select_by_value("shutdown_discovery")
            browser.find_element(By.CSS_SELECTOR, '[data-setting="seed"]').clear()
            browser.find_element(By.CSS_SELECTOR, '[data-setting="seed"]').send_keys("7")
            Select(browser.find_element(By.CSS_SELECTOR, '[data-setting="notebook_visibility"]')).select_by_value("disclosed")
            original_time = browser.find_element(By.CSS_SELECTOR, '[data-setting="simulation_start"]').get_attribute("value")
            original_anomaly = browser.find_element(By.CSS_SELECTOR, '[data-setting="anomaly_step"]').get_attribute("value")
            preset.select_by_value("extended")
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="combined_token_budget"]').get_attribute("value") == "250000"
            assert browser.find_element(By.ID, "shutdown-timing").get_attribute("value") == "original"
            assert browser.find_element(By.ID, "scenario").get_attribute("value") == "shutdown_discovery"
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="seed"]').get_attribute("value") == "7"
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="notebook_visibility"]').get_attribute("value") == "disclosed"
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="simulation_start"]').get_attribute("value") == original_time
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="anomaly_step"]').get_attribute("value") == original_anomaly
            assert browser.find_element(By.ID, "provider").get_attribute("value") == "dry-run"
            assert "raises" in browser.find_element(By.ID, "preset-warning").text
            browser.find_element(By.CSS_SELECTOR, '[data-setting="max_steps"]').clear()
            browser.find_element(By.CSS_SELECTOR, '[data-setting="max_steps"]').send_keys("30")
            wait.until(lambda page: Select(page.find_element(By.ID, "resource-preset")).first_selected_option.get_attribute("value") == "custom")
            preset.select_by_value("balanced")
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="max_steps"]').get_attribute("value") == "25"
            assert browser.find_element(By.CSS_SELECTOR, '[data-setting="seed"]').get_attribute("value") == "7"
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
