(function() {

    function showAlert(message, type = "error") {
        const container = document.getElementById("alertContainer");

        if (!container) {
            console.error("alertContainer not found");
            return;
        }

        const alert = document.createElement("div");
        alert.className = "alert " + type;

        const text = document.createElement("span");
        text.innerText = message;

        const close = document.createElement("span");
        close.className = "alert-close";
        close.innerHTML = "&times;";

        alert.appendChild(text);
        alert.appendChild(close);
        container.appendChild(alert);

        const removeAlert = function() {
            alert.style.animation = "fadeOut 0.3s ease forwards";
            setTimeout(() => alert.remove(), 300);
        };

        const timeout = setTimeout(removeAlert, 3500);

        close.addEventListener("click", function() {
            clearTimeout(timeout);
            removeAlert();
        });
    }

    // 🔥 LOCK IT (cannot be overwritten)
    Object.defineProperty(window, "showAlert", {
        value: showAlert,
        writable: false,
        configurable: false
    });

    console.log("alerts.js locked and loaded");

})();
