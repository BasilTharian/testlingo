package testlingo;

import java.io.File;
import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import io.appium.java_client.AppiumBy;
import io.appium.java_client.AppiumDriver;
import io.appium.java_client.HidesKeyboard;
import io.appium.java_client.InteractsWithApps;
import io.appium.java_client.android.AndroidDriver;
import io.appium.java_client.ios.IOSDriver;
import io.appium.java_client.remote.SupportsRotation;
import io.appium.java_client.remote.options.BaseOptions;
import org.openqa.selenium.By;
import org.openqa.selenium.Dimension;
import org.openqa.selenium.NoSuchElementException;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.ScreenOrientation;
import org.openqa.selenium.WebDriverException;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.PointerInput;
import org.openqa.selenium.interactions.Sequence;
import org.openqa.selenium.remote.RemoteWebElement;
import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;

/**
 * Shared set-up and element finders for Testlingo's generated Appium tests.
 *
 * find("Login") looks for the element by accessibility id, resource id, visible text,
 * hint or content description (Android) or by name / label / value (iOS).
 * An XPath such as "//android.widget.Button[1]" also works.
 */
public abstract class BaseMobileTest {

    protected AppiumDriver driver;
    protected boolean ios;
    protected String appPackage = "";

    /** The device and app to start: filled in by the generated test. */
    protected abstract BaseOptions<?> options();

    /** The Appium server (or cloud device service) address. */
    protected abstract String serverUrl();

    @BeforeMethod
    public void startApp() throws Exception {
        BaseOptions<?> opts = options();
        ios = String.valueOf(opts.getPlatformName()).equalsIgnoreCase("ios");
        var url = URI.create(serverUrl()).toURL();
        driver = ios ? new IOSDriver(url, opts) : new AndroidDriver(url, opts);
        Object pkg = driver.getCapabilities().getCapability("appPackage");
        appPackage = pkg == null ? "" : pkg.toString();
    }

    @AfterMethod(alwaysRun = true)
    public void closeApp(ITestResult result) {
        if (driver == null) {
            return;
        }
        if (!result.isSuccess()) {
            screenshot("FAILED-" + result.getMethod().getMethodName());
        }
        driver.quit();
    }

    // ---------------------------------------------------------------- finding elements

    protected WebElement find(String target) {
        return find(target, 10);
    }

    protected WebElement find(String target, int seconds) {
        List<By> ways = new ArrayList<>();
        String q = target.replace("\"", "\\\"");
        if (target.startsWith("//")) {
            ways.add(AppiumBy.xpath(target));
        } else if (ios) {
            ways.add(AppiumBy.accessibilityId(target));
            ways.add(AppiumBy.iOSNsPredicateString("label == \"" + q + "\" OR name == \"" + q + "\" OR value == \"" + q + "\""));
            ways.add(AppiumBy.iOSNsPredicateString("label CONTAINS[c] \"" + q + "\" OR placeholderValue CONTAINS[c] \"" + q + "\""));
        } else {
            ways.add(AppiumBy.accessibilityId(target));
            ways.add(AppiumBy.id(target.contains(":") ? target : appPackage + ":id/" + target));
            ways.add(AppiumBy.androidUIAutomator("new UiSelector().text(\"" + q + "\")"));
            ways.add(AppiumBy.androidUIAutomator("new UiSelector().textContains(\"" + q + "\")"));
            String x = xq(target);
            ways.add(AppiumBy.xpath("//*[@hint=" + x + " or @content-desc=" + x + " or @resource-id=" + x + "]"));
            ways.add(AppiumBy.androidUIAutomator("new UiSelector().descriptionContains(\"" + q + "\")"));
        }
        long end = System.currentTimeMillis() + seconds * 1000L;
        while (true) {
            for (By by : ways) {
                try {
                    for (WebElement e : driver.findElements(by)) {
                        if (e.isDisplayed()) {
                            return e;
                        }
                    }
                } catch (WebDriverException ignored) {
                    // try the next way of finding it
                }
            }
            if (System.currentTimeMillis() > end) {
                throw new NoSuchElementException("Could not find \"" + target + "\" on the screen");
            }
            pause(500);
        }
    }

    /** A text field by name, or the Nth text field on screen when written as "field 2". */
    protected WebElement field(String target) {
        var m = java.util.regex.Pattern.compile("(?i)^(field|input|textbox)\\s*(\\d+)$").matcher(target);
        if (m.matches()) {
            String cls = ios ? "XCUIElementTypeTextField" : "android.widget.EditText";
            List<WebElement> fields = driver.findElements(AppiumBy.className(cls));
            int i = Integer.parseInt(m.group(2)) - 1;
            Assert.assertTrue(i < fields.size(), "Only " + fields.size() + " text fields on screen");
            return fields.get(i);
        }
        return find(target);
    }

    // ---------------------------------------------------------------- actions

    protected void fill(WebElement field, String text) {
        field.click();
        field.clear();
        field.sendKeys(text);
    }

    protected void longPress(WebElement el) {
        String id = ((RemoteWebElement) el).getId();
        if (ios) {
            driver.executeScript("mobile: touchAndHold", Map.of("elementId", id, "duration", 1.5));
        } else {
            driver.executeScript("mobile: longClickGesture", Map.of("elementId", id, "duration", 1500));
        }
    }

    /** direction: "up", "down", "left" or "right" (the way the finger moves). */
    protected void swipe(String direction) {
        Dimension size = driver.manage().window().getSize();
        int w = size.getWidth();
        int h = size.getHeight();
        int[] p = switch (direction) {
            case "up" -> new int[] {w / 2, h * 3 / 4, w / 2, h / 4};
            case "down" -> new int[] {w / 2, h / 4, w / 2, h * 3 / 4};
            case "left" -> new int[] {w * 4 / 5, h / 2, w / 5, h / 2};
            default -> new int[] {w / 5, h / 2, w * 4 / 5, h / 2};
        };
        PointerInput finger = new PointerInput(PointerInput.Kind.TOUCH, "finger");
        Sequence swipe = new Sequence(finger, 1);
        swipe.addAction(finger.createPointerMove(Duration.ZERO, PointerInput.Origin.viewport(), p[0], p[1]));
        swipe.addAction(finger.createPointerDown(PointerInput.MouseButton.LEFT.asArg()));
        swipe.addAction(finger.createPointerMove(Duration.ofMillis(600), PointerInput.Origin.viewport(), p[2], p[3]));
        swipe.addAction(finger.createPointerUp(PointerInput.MouseButton.LEFT.asArg()));
        driver.perform(List.of(swipe));
    }

    protected void scrollTo(String text) {
        String q = text.replace("\"", "\\\"");
        if (ios) {
            driver.executeScript("mobile: scroll", Map.of("predicateString", "label CONTAINS \"" + q + "\""));
        } else {
            driver.findElement(AppiumBy.androidUIAutomator(
                    "new UiScrollable(new UiSelector().scrollable(true))"
                            + ".scrollIntoView(new UiSelector().textContains(\"" + q + "\"))"));
        }
    }

    protected void hideKeyboard() {
        try {
            ((HidesKeyboard) driver).hideKeyboard();
        } catch (WebDriverException ignored) {
            // the keyboard was already hidden
        }
    }

    protected void rotate(ScreenOrientation orientation) {
        ((SupportsRotation) driver).rotate(orientation);
    }

    protected void runInBackground(int seconds) {
        ((InteractsWithApps) driver).runAppInBackground(Duration.ofSeconds(seconds));
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
            File shot = driver.getScreenshotAs(OutputType.FILE);
            Path dir = Path.of("target", "screenshots");
            Files.createDirectories(dir);
            Files.copy(shot.toPath(), dir.resolve(name + ".png"), StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException | WebDriverException e) {
            System.err.println("Could not save screenshot " + name + ": " + e.getMessage());
        }
    }

    // ---------------------------------------------------------------- checks

    protected void assertDisplayed(String text) {
        try {
            find(text, 8);
        } catch (NoSuchElementException e) {
            Assert.assertTrue(driver.getPageSource().toLowerCase().contains(text.toLowerCase()),
                    "Expected to see \"" + text + "\" on the screen, but it was not found");
        }
    }

    protected void assertNotDisplayed(String text) {
        try {
            find(text, 2);
        } catch (NoSuchElementException e) {
            return;
        }
        Assert.fail("\"" + text + "\" should not be displayed, but it is");
    }

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
