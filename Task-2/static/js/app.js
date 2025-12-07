// Escape HTML helper to prevent XSS
function escapeHtml(unsafe) {
    if (!unsafe) return "";
    return unsafe.replace(/[&<"'>`=\/]/g, function (c) {
        return {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[c];
    });
}

// ---------- GLOBAL CHART HANDLES ----------
let ratingChart = null;
let trendChart = null;

// ---------- ADMIN DASHBOARD FUNCTIONS ----------

async function fetchStats() {
    const res = await fetch("/api/stats");
    if (!res.ok) return null;
    return await res.json();
}

async function loadStatsAndCharts() {
    const stats = await fetchStats();
    if (!stats) return;

    const totalEl = document.getElementById("total");
    const avgEl = document.getElementById("avg");
    if (totalEl) totalEl.innerText = stats.total;
    if (avgEl) avgEl.innerText = stats.avg_rating;

    // Rating distribution: stats.rating_counts => [count1,count2,..,count5]
    const ratingCounts = stats.rating_counts || [0,0,0,0,0];
    renderOrUpdateRatingChart(ratingCounts);

    // Trend chart: labels and counts
    const labels = stats.trend_labels || [];
    const counts = stats.trend_counts || [];
    renderOrUpdateTrendChart(labels, counts);
}

function renderOrUpdateRatingChart(counts) {
    const ctx = document.getElementById("ratingChart").getContext("2d");
    const labels = ["1 ★","2 ★","3 ★","4 ★","5 ★"];

    if (ratingChart) {
        ratingChart.data.datasets[0].data = counts;
        ratingChart.update();
        return;
    }

    ratingChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Number of reviews',
                data: counts,
                borderWidth: 1
            }]
        },
        options: {
            scales: {
                y: { beginAtZero: true, ticks: { precision:0 } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

function renderOrUpdateTrendChart(labels, data) {
    const ctx = document.getElementById("trendChart").getContext("2d");

    if (trendChart) {
        trendChart.data.labels = labels;
        trendChart.data.datasets[0].data = data;
        trendChart.update();
        return;
    }

    trendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Reviews per day',
                data: data,
                fill: true,
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 3
            }]
        },
        options: {
            scales: {
                y: { beginAtZero: true, ticks: { precision:0 } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

async function loadReviews() {
    const res = await fetch("/api/reviews");
    const arr = await res.json();

    const container = document.getElementById("reviews");
    if (!container) return;

    container.innerHTML = "";
    arr.slice().reverse().forEach(r => {
        const div = document.createElement("div");
        div.className = "card";

        div.innerHTML = `
            <p><b>Rating:</b> ${escapeHtml(String(r.rating))} &nbsp; <small>${new Date(r.ts*1000).toLocaleString()}</small></p>
            <p><b>Review:</b> ${escapeHtml(r.review)}</p>
            <p><b>AI Summary:</b> ${escapeHtml(r.summary)}</p>
            <p><b>AI Actions:</b></p>
            <ul>
                ${ (r.actions || []).map(a => `<li>${escapeHtml(a)}</li>`).join("") }
            </ul>
            <hr>
        `;
        container.appendChild(div);
    });
}

// Live updates with Server-Sent Events (SSE)
if (typeof authorized !== 'undefined' && authorized) {
    // initial load
    loadStatsAndCharts();
    loadReviews();

    // SSE stream
    const evtSource = new EventSource('/stream');
    evtSource.onmessage = function(e){
        try {
            const review = JSON.parse(e.data);
            // Reload list and stats for simplicity (small dataset)
            loadReviews();
            loadStatsAndCharts();
        } catch (err) { console.error("SSE parse error", err); }
    };
}

// ---------- USER PAGE FUNCTIONS ----------
document.addEventListener("DOMContentLoaded", function(){
    const submitBtn = document.getElementById("submitBtn");
    if (submitBtn) {
        submitBtn.addEventListener("click", async function () {
            const rating = document.getElementById("rating").value;
            const review = document.getElementById("review").value;

            const res = await fetch("/api/submit", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ rating, review })
            });

            const data = await res.json();
            if (data && data.ai_reply) {
                document.getElementById("aiReply").innerText = data.ai_reply;
                document.getElementById("responseBox").style.display = "block";
                // If admin open, SSE will pick up and charts will update automatically
            } else {
                document.getElementById("aiReply").innerText = data.error || "Submission failed";
                document.getElementById("responseBox").style.display = "block";
            }

            // clear textarea
            document.getElementById("review").value = "";
        });
    }
});
