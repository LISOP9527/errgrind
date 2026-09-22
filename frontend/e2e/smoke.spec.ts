import { expect, test } from "@playwright/test";

test("workspace and settings render without horizontal overflow", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "ErrGrind" })).toBeAttached();

  await page.goto("/config");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByLabel("提供商 (Provider)")).toBeVisible();
  await expect(page.getByRole("status")).toContainText(
    /已保存|待保存|保存中|保存失败/,
  );

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );
  expect(hasHorizontalOverflow).toBe(false);
});
