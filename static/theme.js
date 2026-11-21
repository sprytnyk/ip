// The light/dark switch in the header. The page sets the theme before it's
// drawn (see the head of the templates); this only handles the button.
const themeButton = document.getElementById("theme");
const root = document.documentElement;

const labelThemeButton = () => {
    const label = root.dataset.theme === "dark"
        ? "Switch to the light theme"
        : "Switch to the dark theme";
    themeButton.setAttribute("aria-label", label);
    themeButton.title = label;
};

labelThemeButton();
themeButton.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    // Storage can be blocked, and then the page simply forgets the choice
    try {
        localStorage.setItem("ip-theme", root.dataset.theme);
    } catch {}
    labelThemeButton();
});
