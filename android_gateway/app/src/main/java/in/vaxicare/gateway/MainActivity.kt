package in.vaxicare.gateway

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {

    private lateinit var urlInput: EditText
    private lateinit var keyInput: EditText
    private lateinit var statusText: TextView
    private lateinit var startButton: Button
    private lateinit var syncButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Simple runtime permission request for SEND_SMS
        if (checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.SEND_SMS), 101)
        }

        // Programmatic healthcare layout
        val layout = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(40, 60, 40, 40)
        }

        val title = TextView(this).apply {
            text = "VaxiCare SMS Companion Gateway"
            textSize = 20f
            setTypeface(null, android.graphics.Typeface.BOLD)
            setTextColor(android.graphics.Color.parseColor("#006934"))
            setPadding(0, 0, 0, 30)
        }
        layout.addView(title)

        urlInput = EditText(this).apply {
            hint = "VaxiCare Server URL (e.g. https://phc.vaxicare.in)"
            setText("http://10.0.2.2:5000")
        }
        layout.addView(urlInput)

        keyInput = EditText(this).apply {
            hint = "Gateway API Key"
        }
        layout.addView(keyInput)

        statusText = TextView(this).apply {
            text = "Status: Idle (Not connected)"
            setPadding(0, 30, 0, 30)
        }
        layout.addView(statusText)

        syncButton = Button(this).apply {
            text = "Test Connection & Sync Now"
            setBackgroundColor(android.graphics.Color.parseColor("#006934"))
            setTextColor(android.graphics.Color.WHITE)
            setOnClickListener {
                triggerSync()
            }
        }
        layout.addView(syncButton)

        setContentView(layout)
    }

    private fun triggerSync() {
        val url = urlInput.text.toString().trim()
        val key = keyInput.text.toString().trim()
        if (key.isEmpty()) {
            Toast.makeText(this, "Please enter your Gateway API Key from Admin Center.", Toast.LENGTH_SHORT).show()
            return
        }

        statusText.text = "Status: Synchronizing with VaxiCare..."
        Thread {
            try {
                val service = GatewayApiService(url, key)
                val worker = SmsDispatchWorker(this, service)
                worker.processPendingQueue()
                runOnUiThread {
                    statusText.text = "Status: Connected. Queue checked successfully."
                    Toast.makeText(this, "Queue sync complete.", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    statusText.text = "Status: Error - ${e.message}"
                    Toast.makeText(this, "Error: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }
}
