package in.vaxicare.gateway

import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class OutboundMessage(
    val uuid: String,
    val recipient: String,
    val text: String
)

class GatewayApiService(
    private val baseUrl: String,
    private val apiKey: String
) {

    fun fetchPendingMessages(limit: Int = 10): List<OutboundMessage> {
        val endpoint = URL("$baseUrl/api/gateway/pending?limit=$limit")
        val conn = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            setRequestProperty("X-Gateway-Key", apiKey)
            setRequestProperty("Accept", "application/json")
            connectTimeout = 10000
            readTimeout = 15000
        }

        if (conn.responseCode != 200) {
            throw RuntimeException("Gateway fetch failed with HTTP ${conn.responseCode}")
        }

        val reader = BufferedReader(InputStreamReader(conn.inputStream))
        val response = reader.readText()
        reader.close()

        val json = JSONObject(response)
        val array = json.optJSONArray("messages") ?: return emptyList()
        val list = mutableListOf<OutboundMessage>()
        for (i in 0 until array.length()) {
            val item = array.getJSONObject(i)
            list.add(
                OutboundMessage(
                    uuid = item.getString("uuid"),
                    recipient = item.getString("recipient"),
                    text = item.getString("text")
                )
            )
        }
        return list
    }

    fun reportStatus(uuid: String, status: String, error: String? = null, deliveryConfirmed: Boolean = false): Boolean {
        val endpoint = URL("$baseUrl/api/gateway/status")
        val conn = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            setRequestProperty("X-Gateway-Key", apiKey)
            setRequestProperty("Content-Type", "application/json")
            doOutput = true
            connectTimeout = 10000
            readTimeout = 15000
        }

        val payload = JSONObject().apply {
            put("uuid", uuid)
            put("status", status)
            if (error != null) put("error", error)
            put("delivery_confirmed", deliveryConfirmed)
        }

        val writer = OutputStreamWriter(conn.outputStream)
        writer.write(payload.toString())
        writer.flush()
        writer.close()

        return conn.responseCode == 200
    }

    fun sendHeartbeat(): Boolean {
        val endpoint = URL("$baseUrl/api/gateway/heartbeat")
        val conn = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            setRequestProperty("X-Gateway-Key", apiKey)
            connectTimeout = 8000
            readTimeout = 8000
        }
        return conn.responseCode == 200
    }
}
