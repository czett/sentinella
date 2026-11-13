(function () {
    var theme = "light";
    try {
        if (window.localStorage.getItem("sentinella_theme") === "dark") {
            theme = "dark";
        }
    } catch (error) {
        theme = "light";
    }
    document.documentElement.setAttribute("data-theme", theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
        meta.setAttribute("content", theme === "dark" ? "#09090b" : "#f8f8f9");
    }
    var scheme = document.querySelector('meta[name="color-scheme"]');
    if (scheme) {
        scheme.setAttribute("content", theme);
    }
    try {
        if (window.localStorage.getItem("sentinella_animations") === "off") {
            document.documentElement.classList.add("disable-animations");
        }
    } catch (error) {}
})();
