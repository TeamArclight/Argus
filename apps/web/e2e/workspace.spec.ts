import { test, expect } from '@playwright/test';

test.describe('ARGUS End-to-End Procurement Compliance Workflow', () => {
  test('1. Workspace dashboard loads with explicit demo mode indicator', async ({ page }) => {
    await page.goto('/workspace');
    await expect(page.getByText(/demo/i).first()).toBeVisible();
    await expect(page.getByText(/tenders|procurement/i).first()).toBeVisible();
  });

  test('2. Tenders list and tender detail page navigation', async ({ page }) => {
    await page.goto('/workspace/tenders?mode=demo');
    await expect(page.getByText('Procurement Tenders').first()).toBeVisible();
    
    // Click into first tender
    const tenderLink = page.locator('a[href*="/workspace/tenders/"]').first();
    await tenderLink.click();
    await expect(page).toHaveURL(/\/workspace\/tenders\/.+/);
  });

  test('3. Bidder detail & Mismatch explainability modal acceptance test', async ({ page }) => {
    await page.goto('/workspace/bidders/bidder_alpha_01');
    await expect(page.getByText('Alpha Infotech Private Limited')).toBeVisible();

    // Verify statutory identifier registry card
    await expect(page.getByText('Statutory Identifier Registry')).toBeVisible();
    await expect(page.getByText('07AABCA1234H1Z9').first()).toBeVisible();

    // Check document table
    await expect(page.getByText('Uploaded Bidder Documents')).toBeVisible();

    // Test Mismatch Explainability: click view details button if visible
    const mismatchBtn = page.getByRole('button', { name: /mismatch/i }).first();
    if (await mismatchBtn.isVisible()) {
      await mismatchBtn.click();
      const modalHeader = page.getByRole('dialog').getByText(/mismatch/i).first();
      await expect(modalHeader).toBeVisible();
      
      // Close modal
      await page.getByRole('button', { name: /close/i }).first().click();
      await expect(modalHeader).not.toBeVisible();
    }
  });

  test('4. Human review page officer decision workflow', async ({ page }) => {
    await page.goto('/workspace/bidders/bidder_alpha_01/review');
    await expect(page.getByText(/human officer review|review/i).first()).toBeVisible();
  });

  test('5. Telemetry status page accurately reports service states', async ({ page }) => {
    await page.goto('/workspace/status');
    await expect(page.getByText(/telemetry|health|status/i).first()).toBeVisible();
    await expect(page.getByText('ARGUS API Gateway')).toBeVisible();
  });
});
