package testlingo;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

import org.openqa.selenium.By;
import org.openqa.selenium.NoSuchElementException;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.TakesScreenshot;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebDriverException;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;

/**
 * Shared set-up and element finders for Testlingo's generated Selenium tests.
 *
 * Elements are found the same way Testlingo finds them:
 *   field("Username")   - a text box / drop-down by its label, aria-label, placeholder, name or id
 *   clickable("Login")  - a button, link, tab or menu item by its visible text
 *   element("Remember") - either of the above
 * A CSS selector ("#submit", ".item") or XPath ("//button[2]") also works everywhere.
 *
 * Watch the browser while it runs:  mvn test -Dheaded=true
 */
public abstract class BaseWebTest {

    protected static final Duration TIMEOUT = Duration.ofSeconds(10);

    protected WebDriver driver;
    protected WebDriverWait wait;

    @BeforeMethod
    public void startBrowser() {
        ChromeOptions options = new ChromeOptions();
        if (!Boolean.getBoolean("headed")) {
            options.addArguments("--headless=new");
        }
        options.addArguments("--window-size=1280,800", "--no-sandbox", "--disable-dev-shm-usage");
        driver = new ChromeDriver(options); // Selenium Manager downloads the matching ChromeDriver
        wait = new WebDriverWait(driver, TIMEOUT);
    }

    @AfterMethod(alwaysRun = true)
    public void closeBrowser(ITestResult result) {
        if (driver == null) {
            return;
        }
        if (!result.isSuccess()) {
            screenshot("FAILED-" + result.getMethod().getMethodName());
        }
        driver.quit();
    }

    // ---------------------------------------------------------------- finding elements

    /** A text box, text area or drop-down, found by label, aria-label, placeholder, name or id. */
    protected WebElement field(String name) {
        return firstVisible(name, fieldLocators(name));
    }

    /** A button, link, tab or menu item, found by its visible text, value, title or alt text. */
    protected WebElement clickable(String text) {
        return firstVisible(text, clickLocators(text));
    }

    /** Anything: tries the field rules first, then the clickable rules. */
    protected WebElement element(String target) {
        List<By> all = new ArrayList<>(fieldLocators(target));
        all.addAll(clickLocators(target));
        return firstVisible(target, all);
    }

    protected List<By> fieldLocators(String name) {
        if (isSelector(name)) {
            return List.of(selector(name));
        }
        String n = xq(name.toLowerCase());
        return List.of(
                By.xpath("//*[@id=//label[contains(" + lc("normalize-space(.)") + ", " + n + ")]/@for]"),
                By.xpath("//label[contains(" + lc("normalize-space(.)") + ", " + n + ")]"
                        + "//*[self::input or self::textarea or self::select]"),
                By.xpath("//*[self::input or self::textarea or self::select][contains(" + lc("@aria-label") + ", " + n + ")]"),
                By.xpath("//*[self::input or self::textarea][contains(" + lc("@placeholder") + ", " + n + ")]"),
                By.name(name),
                By.id(name));
    }

    protected List<By> clickLocators(String text) {
        if (isSelector(text)) {
            return List.of(selector(text));
        }
        String t = xq(text);
        String tl = xq(text.toLowerCase());
        String clickables = "//*[self::button or self::a or @role='button' or @role='link' or @role='tab' or @role='menuitem']";
        return List.of(
                By.xpath(clickables + "[normalize-space(.)=" + t + "]"),
                By.xpath("//input[@type='submit' or @type='button' or @type='reset'][@value=" + t + "]"),
                By.xpath(clickables + "[contains(" + lc("normalize-space(.)") + ", " + tl + ")"
                        + " or contains(" + lc("@aria-label") + ", " + tl + ")]"),
                By.xpath("//*[normalize-space(text())=" + t + "]"),
                By.xpath("//*[contains(" + lc("text()") + ", " + tl + ")]"),
                By.xpath("//*[@title=" + t + " or @alt=" + t + "]"),
                By.name(text),
                By.id(text));
    }

    /** Waits up to TIMEOUT and returns the first visible element any of the locators finds. */
    protected WebElement firstVisible(String what, List<By> locators) {
        long end = System.currentTimeMillis() + TIMEOUT.toMillis();
        while (true) {
            for (By by : locators) {
                try {
                    for (WebElement e : driver.findElements(by)) {
                        if (e.isDisplayed()) {
                            return e;
                        }
                    }
                } catch (WebDriverException ignored) {
                    // this way of finding it doesn't apply here; try the next one
                }
            }
            if (System.currentTimeMillis() > end) {
                throw new NoSuchElementException("Could not find \"" + what + "\" on the page");
            }
            pause(250);
        }
    }

    // ---------------------------------------------------------------- actions

    protected void fill(WebElement field, String text) {
        field.clear();
        field.sendKeys(text);
    }

    protected void setChecked(WebElement box, boolean checked) {
        if (box.isSelected() != checked) {
            box.click();
        }
    }

    protected void pause(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** Saves target/screenshots/NAME.png */
    protected void screenshot(String name) {
        try {
            File shot = ((TakesScreenshot) driver).getScreenshotAs(OutputType.FILE);
            Path dir = Path.of("target", "screenshots");
            Files.createDirectories(dir);
            Files.copy(shot.toPath(), dir.resolve(name + ".png"), StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException | WebDriverException e) {
            System.err.println("Could not save screenshot " + name + ": " + e.getMessage());
        }
    }

    // ---------------------------------------------------------------- checks

    protected String pageText() {
        return driver.findElement(By.tagName("body")).getText();
    }

    protected void waitForText(String text) {
        String t = text.toLowerCase();
        new WebDriverWait(driver, Duration.ofSeconds(15)).until(d -> pageText().toLowerCase().contains(t));
    }

    /** Passes when the text appears on the page within 5 seconds. */
    protected void assertPageContains(String text) {
        String t = text.toLowerCase();
        long end = System.currentTimeMillis() + 5000;
        while (System.currentTimeMillis() < end) {
            if (pageText().toLowerCase().contains(t)) {
                return;
            }
            pause(300);
        }
        Assert.fail("Expected to see \"" + text + "\" on the page, but it was not found");
    }

    protected void assertPageNotContains(String text) {
        Assert.assertFalse(pageText().toLowerCase().contains(text.toLowerCase()),
                "Page should not contain \"" + text + "\", but it does");
    }

    // ---------------------------------------------------------------- helpers

    protected static boolean isSelector(String s) {
        return s.startsWith("#") || s.startsWith(".") || s.startsWith("[") || s.startsWith("//")
                || s.startsWith("css=") || s.startsWith("xpath=");
    }

    protected static By selector(String s) {
        if (s.startsWith("xpath=")) {
            return By.xpath(s.substring(6));
        }
        if (s.startsWith("//")) {
            return By.xpath(s);
        }
        return By.cssSelector(s.startsWith("css=") ? s.substring(4) : s);
    }

    /** Lower-cases an XPath expression (XPath 1.0 has no lower-case()). */
    protected static String lc(String expr) {
        return "translate(" + expr + ", 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')";
    }

    /** Quotes a string for XPath, including strings that contain both kinds of quote. */
    protected static String xq(String s) {
        if (!s.contains("'")) {
            return "'" + s + "'";
        }
        if (!s.contains("\"")) {
            return "\"" + s + "\"";
        }
        return "concat('" + s.replace("'", "', \"'\", '") + "')";
    }
}
