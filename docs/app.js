// ============================================================
// CANBERRA TRAFFIC FORECAST — FRONTEND
// ============================================================

const DATA_URL = "https://raw.githubusercontent.com/shubhankaranagal/gb-electricity-imbalance-forecasting/live-data/docs/data/latest_forecasts.json";

const CANBERRA_CENTRE = [-35.2809, 149.1300];

const HORIZON_KEYS = {
    now: "current",
    15: "forecast_15",
    30: "forecast_30",
    60: "forecast_60",
    120: "forecast_120"
};

const HORIZON_LABELS = {
    now: "Current conditions",
    15: "15-minute forecast",
    30: "30-minute forecast",
    60: "60-minute forecast",
    120: "120-minute forecast"
};


// ============================================================
// STATE
// ============================================================

let forecastData = null;
let selectedHorizon = "now";
let selectedLink = null;

const roadLayers = [];


// ============================================================
// MAP
// ============================================================

const map = L.map(
    "map",
    {
        zoomControl: false,
        preferCanvas: true
    }
).setView(
    CANBERRA_CENTRE,
    12
);

L.control.zoom({
    position: "bottomright"
}).addTo(map);


// Dark basemap
const CARTO_API_KEY = "cb1_4eti_1_2667f3f72acae383e1124b53";

L.tileLayer(
    `https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=${CARTO_API_KEY}`,
    {
        maxZoom: 20,
        attribution:
            '&copy; OpenStreetMap contributors &copy; CARTO'
    }
).addTo(map);


// ============================================================
// DOM
// ============================================================

const statusDot =
    document.getElementById("status-dot");

const statusMain =
    document.getElementById("status-main");

const statusSub =
    document.getElementById("status-sub");

const selectedTime =
    document.getElementById("selected-time");

const roadPanel =
    document.getElementById("road-panel");

const roadName =
    document.getElementById("road-name");

const roadCurrent =
    document.getElementById("road-current");

const roadCurrentRelative =
    document.getElementById("road-current-relative");

const roadForecastRelative =
    document.getElementById("road-forecast-relative");

const roadTravelTime =
    document.getElementById("road-travel-time");

const roadForecast =
    document.getElementById("road-forecast");

const roadForecastLabel =
    document.getElementById(
        "road-forecast-label"
    );

const roadChange =
    document.getElementById("road-change");

const roadModelStatus =
    document.getElementById(
        "road-model-status"
    );

const closeRoadPanel =
    document.getElementById(
        "close-road-panel"
    );

const horizonButtons =
    document.querySelectorAll(
        ".horizon-button"
    );


// ============================================================
// FORMATTING
// ============================================================

function formatSeconds(value) {

    if (
        value === null ||
        value === undefined ||
        !Number.isFinite(value)
    ) {
        return "—";
    }

    const sign = value < 0 ? "−" : "";
    const seconds = Math.round(Math.abs(value));

    if (seconds < 60) {
        return `${sign}${seconds} sec`;
    }

    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;

    return remainder === 0
        ? `${sign}${minutes} min`
        : `${sign}${minutes} min ${remainder} sec`;
}

function formatPercent(value) {

    if (
        value === null ||
        value === undefined ||
        !Number.isFinite(value)
    ) {
        return "—";
    }

    const percent = value * 100;

    const sign =
        percent > 0 ? "+" : "";

    return `${sign}${percent.toFixed(0)}%`;
}


function formatChange(value) {

    if (
        value === null ||
        value === undefined ||
        !Number.isFinite(value)
    ) {
        return "—";
    }

    const pp = value * 100;

    if (Math.abs(pp) < 0.5) {
        return "≈ no change";
    }

    const sign =
        pp > 0 ? "+" : "";

    return `${sign}${pp.toFixed(0)} pp`;
}


function formatCanberraTime(
    isoString,
    includeDate = false
) {

    if (!isoString) {
        return "Unknown";
    }

    const date = new Date(isoString);

    if (
        Number.isNaN(
            date.getTime()
        )
    ) {
        return "Unknown";
    }

    const options = {
        timeZone: "Australia/Sydney",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
    };

    if (includeDate) {
        options.day = "2-digit";
        options.month = "short";
    }

    return new Intl.DateTimeFormat(
        "en-AU",
        options
    ).format(date);
}


function freshnessText(isoString) {

    if (!isoString) {
        return "Unknown data age";
    }

    const timestamp =
        new Date(isoString);

    const now = new Date();

    const minutes = Math.max(
        0,
        Math.round(
            (
                now.getTime()
                - timestamp.getTime()
            )
            / 60000
        )
    );

    if (minutes < 1) {
        return "just now";
    }

    if (minutes === 1) {
        return "1 min ago";
    }

    if (minutes < 60) {
        return `${minutes} min ago`;
    }

    const hours =
        Math.floor(minutes / 60);

    const remainder =
        minutes % 60;

    if (remainder === 0) {
        return `${hours}h ago`;
    }

    return `${hours}h ${remainder}m ago`;
}


// ============================================================
// CONGESTION COLOUR SCALE
// ============================================================

function interpolateColour(
    c1,
    c2,
    t
) {

    const a = {
        r: parseInt(c1.slice(1, 3), 16),
        g: parseInt(c1.slice(3, 5), 16),
        b: parseInt(c1.slice(5, 7), 16)
    };

    const b = {
        r: parseInt(c2.slice(1, 3), 16),
        g: parseInt(c2.slice(3, 5), 16),
        b: parseInt(c2.slice(5, 7), 16)
    };

    const r = Math.round(
        a.r + (b.r - a.r) * t
    );

    const g = Math.round(
        a.g + (b.g - a.g) * t
    );

    const bl = Math.round(
        a.b + (b.b - a.b) * t
    );

    return (
        "#"
        + r.toString(16)
            .padStart(2, "0")
        + g.toString(16)
            .padStart(2, "0")
        + bl.toString(16)
            .padStart(2, "0")
    );
}


function congestionColour(value) {

    if (
        value === null ||
        value === undefined ||
        !Number.isFinite(value)
    ) {
        return "#687483";
    }

    /*
       Relative delay:

       <= 0%     green
       +30%      yellow-green
       +50%      yellow
       +75%      orange
       >= 100%   red

       Values above 100% remain red rather
       than distorting the visual scale.
    */

    const stops = [
        [0.00, "#37c978"],
        [0.30, "#b8d94a"],
        [0.50, "#f2c94c"],
        [0.75, "#ef8c3a"],
        [1.00, "#e84b4b"]
    ];

    const x = Math.max(
        0,
        Math.min(1, value)
    );

    for (
        let i = 0;
        i < stops.length - 1;
        i++
    ) {

        const [
            x1,
            c1
        ] = stops[i];

        const [
            x2,
            c2
        ] = stops[i + 1];

        if (
            x >= x1 &&
            x <= x2
        ) {

            const t =
                (x - x1)
                / (x2 - x1);

            return interpolateColour(
                c1,
                c2,
                t
            );
        }
    }

    return stops[
        stops.length - 1
    ][1];
}


// ============================================================
// GEOMETRY
// ============================================================

function geometryToLatLngs(
    coordinates
) {

    if (
        !Array.isArray(coordinates)
    ) {
        return [];
    }

    /*
       GeoJSON coordinates are:

           [longitude, latitude]

       Leaflet expects:

           [latitude, longitude]
    */

    return coordinates
        .filter(
            point =>
                Array.isArray(point) &&
                point.length >= 2 &&
                Number.isFinite(point[0]) &&
                Number.isFinite(point[1])
        )
        .map(
            point => [
                point[1],
                point[0]
            ]
        );
}


// ============================================================
// CURRENT DISPLAY VALUE
// ============================================================

function valueForLink(link) {

    const key =
        HORIZON_KEYS[
            selectedHorizon
        ];

    return link[key];
}


// ============================================================
// DRAW NETWORK
// ============================================================

function drawRoads() {

    const bounds = [];

    for (
        const link
        of forecastData.links
    ) {

        const latLngs =
            geometryToLatLngs(
                link.geometry
            );

        if (
            latLngs.length < 2
        ) {
            continue;
        }

        const value =
            valueForLink(link);

        const layer =
            L.polyline(
                latLngs,
                {
                    color:
                        congestionColour(
                            value
                        ),

                    weight: 4,

                    opacity:
                        link.status
                        === "data_unavailable"
                            ? 0.45
                            : 0.90,

                    lineCap: "round",
                    lineJoin: "round",

                    className:
                        "traffic-road"
                }
            );

        layer.linkData = link;

        layer.on(
            "mouseover",
            () => {

                layer.setStyle({
                    weight: 7,
                    opacity: 1
                });

                layer.bringToFront();
            }
        );

        layer.on(
            "mouseout",
            () => {

                const isSelected =
                    selectedLink
                    === link;

                layer.setStyle({
                    weight:
                        isSelected
                            ? 7
                            : 4,

                    opacity:
                        link.status
                        === "data_unavailable"
                            ? 0.45
                            : 0.90
                });
            }
        );

        layer.on(
            "click",
            () => {

                selectedLink =
                    link;

                updateRoadPanel();

                updateRoadStyles();
            }
        );

        layer.addTo(map);

        roadLayers.push(layer);

        bounds.push(...latLngs);
    }

    if (bounds.length > 0) {

        map.fitBounds(
            bounds,
            {
                paddingTopLeft:
                    [40, 115],

                paddingBottomRight:
                    [40, 40]
            }
        );
    }
}


// ============================================================
// UPDATE ROAD STYLES
// ============================================================

function updateRoadStyles() {

    for (
        const layer
        of roadLayers
    ) {

        const link =
            layer.linkData;

        const value =
            valueForLink(link);

        const isSelected =
            selectedLink === link;

        layer.setStyle({
            color:
                congestionColour(
                    value
                ),

            weight:
                isSelected
                    ? 7
                    : 4,

            opacity:
                link.status
                === "data_unavailable"
                    ? 0.45
                    : 0.90
        });

        if (isSelected) {
            layer.bringToFront();
        }
    }
}


// ============================================================
// ROAD DETAIL PANEL
// ============================================================

function updateRoadPanel() {

    if (!selectedLink) {
        roadPanel.classList.add("hidden");
        return;
    }

    const link = selectedLink;

    roadPanel.classList.remove("hidden");

    roadName.textContent =
        link.name || "Unnamed road segment";

    roadCurrent.textContent =
        formatSeconds(link.current_delay_seconds);

    roadCurrentRelative.textContent =
        formatPercent(link.current) + " vs free flow";

    const horizon = selectedHorizon;

    const forecastRelative =
        horizon === "now"
            ? link.current
            : link[`forecast_${horizon}`];

    const forecastDelay =
        horizon === "now"
            ? link.current_delay_seconds
            : link[`forecast_${horizon}_delay_seconds`];

    const forecastTravelTime =
        horizon === "now"
            ? link.current_travel_time_seconds
            : link[`forecast_${horizon}_travel_time_seconds`];

    roadForecastLabel.textContent =
        horizon === "now"
            ? "CURRENT DELAY"
            : `+${horizon} MIN FORECAST`;

    roadForecast.textContent =
        formatSeconds(forecastDelay);

    roadForecastRelative.textContent =
        formatPercent(forecastRelative) + " vs free flow";

    roadTravelTime.textContent =
        formatSeconds(forecastTravelTime);

    roadChange.className = "";

    if (
        horizon === "now" ||
        forecastDelay === null ||
        forecastDelay === undefined ||
        link.current_delay_seconds === null ||
        link.current_delay_seconds === undefined
    ) {

        roadChange.textContent = "—";

    } else {

        const change =
            forecastDelay - link.current_delay_seconds;

        const sign =
            change > 0 ? "+" : "";

        roadChange.textContent =
            Math.abs(change) < 0.5
                ? "≈ no change"
                : `${sign}${formatSeconds(change)}`;

        if (change < -0.5) {
            roadChange.className = "improving";
        } else if (change > 0.5) {
            roadChange.className = "worsening";
        } else {
            roadChange.className = "stable";
        }
    }

    if (link.status === "full_history") {

        roadModelStatus.textContent =
            "Full recent traffic history available.";

    } else if (link.status === "warming_up") {

        roadModelStatus.textContent =
            "Forecast warming up — recent lag history is still accumulating.";

    } else {

        roadModelStatus.textContent =
            "Current traffic measurement unavailable for this segment.";
    }
}

// ============================================================
// HORIZON SELECTION
// ============================================================

function selectHorizon(
    horizon
) {

    selectedHorizon =
        horizon;

    for (
        const button
        of horizonButtons
    ) {

        button.classList.toggle(
            "active",
            button.dataset.horizon
            === horizon
        );
    }

    selectedTime.textContent =
        HORIZON_LABELS[
            horizon
        ];

    updateRoadStyles();

    updateRoadPanel();
}


for (
    const button
    of horizonButtons
) {

    button.addEventListener(
        "click",
        () => {

            selectHorizon(
                button.dataset.horizon
            );
        }
    );
}


closeRoadPanel.addEventListener(
    "click",
    () => {

        selectedLink = null;

        updateRoadPanel();

        updateRoadStyles();
    }
);


// ============================================================
// STATUS
// ============================================================

function updateStatus() {

    const trafficTimestamp =
        forecastData.traffic_timestamp;

    const trafficDate =
        new Date(
            trafficTimestamp
        );

    const ageMinutes =
        (
            Date.now()
            - trafficDate.getTime()
        )
        / 60000;

    statusDot.classList.remove(
        "live",
        "stale",
        "error"
    );

    if (
        Number.isFinite(ageMinutes)
        && ageMinutes <= 20
    ) {

        statusDot.classList.add(
            "live"
        );

    } else {

        statusDot.classList.add(
            "stale"
        );
    }

    statusMain.textContent =
        `Traffic ${formatCanberraTime(
            trafficTimestamp
        )} Canberra`;

    const fullHistory =
        forecastData.status_counts
            ?.full_history
        || 0;

    const warming =
        forecastData.status_counts
            ?.warming_up
        || 0;

    const unavailable =
        forecastData.status_counts
            ?.data_unavailable
        || 0;

    let modelText;

    if (
        fullHistory > 0
    ) {

        modelText =
            `${fullHistory} links with full history`;

    } else if (
        warming > 0
    ) {

        modelText =
            "Model history warming up";

    } else {

        modelText =
            "Forecast feed active";
    }

    if (
        unavailable > 0
    ) {

        modelText +=
            ` · ${unavailable} unavailable`;
    }

    statusSub.textContent =
        `${freshnessText(
            trafficTimestamp
        )} · ${modelText}`;
}


// ============================================================
// LOAD DATA
// ============================================================

async function loadForecastData() {

    document.body.classList.add(
        "loading"
    );

    try {

        /*
           Cache-busting query prevents a browser or
           CDN from leaving the map on an old forecast.
        */

        const response =
            await fetch(
                `${DATA_URL}?t=${Date.now()}`,
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {

            throw new Error(
                `Forecast feed returned HTTP ${response.status}`
            );
        }

        forecastData =
            await response.json();

        if (
            !forecastData ||
            !Array.isArray(
                forecastData.links
            )
        ) {

            throw new Error(
                "Invalid forecast JSON."
            );
        }

        updateStatus();

        drawRoads();

        document.body.classList.remove(
            "loading"
        );

        console.log(
            `Loaded ${forecastData.links.length} Canberra road links.`
        );

    } catch (error) {

        console.error(
            "Failed to load forecast data:",
            error
        );

        statusDot.classList.remove(
            "live",
            "stale"
        );

        statusDot.classList.add(
            "error"
        );

        statusMain.textContent =
            "Forecast feed unavailable";

        statusSub.textContent =
            "Could not load live traffic data";

        document.body.classList.remove(
            "loading"
        );
    }
}


// ============================================================
// START
// ============================================================

loadForecastData();
