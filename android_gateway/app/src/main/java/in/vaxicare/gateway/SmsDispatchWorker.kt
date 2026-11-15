package in.vaxicare.gateway

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.telephony.SmsManager
import android.util.Log

class SmsDispatchWorker(
    private val context: Context,
    private val apiService: GatewayApiService
) {
    companion object {
        private const val TAG = "VaxiCareSmsWorker"
        const val ACTION_SMS_SENT = "in.vaxicare.gateway.SMS_SENT"
        const val ACTION_SMS_DELIVERED = "in.vaxicare.gateway.SMS_DELIVERED"
    }

    /**
     * Executes one cycle of pending message polling and dispatch.
     * Handles both direct SmsManager transmission and carrier delivery tracking.
     */
    fun processPendingQueue() {
        try {
            val messages = apiService.fetchPendingMessages(limit = 10)
            Log.i(TAG, "Retrieved ${messages.size} pending SMS items from VaxiCare.")

            val smsManager = SmsManager.getDefault()

            for (msg in messages) {
                try {
                    val sentIntent = PendingIntent.getBroadcast(
                        context,
                        msg.uuid.hashCode(),
                        Intent(ACTION_SMS_SENT).putExtra("uuid", msg.uuid),
                        PendingIntent.FLAG_IMMUTABLE
                    )

                    val deliveryIntent = PendingIntent.getBroadcast(
                        context,
                        msg.uuid.hashCode(),
                        Intent(ACTION_SMS_DELIVERED).putExtra("uuid", msg.uuid),
                        PendingIntent.FLAG_IMMUTABLE
                    )

                    // Dispatch via cellular SIM
                    val parts = smsManager.divideMessage(msg.text)
                    if (parts.size > 1) {
                        val sentIntents = ArrayList<PendingIntent>().apply { add(sentIntent) }
                        val deliveryIntents = ArrayList<PendingIntent>().apply { add(deliveryIntent) }
                        smsManager.sendMultipartTextMessage(msg.recipient, null, parts, sentIntents, deliveryIntents)
                    } else {
                        smsManager.sendTextMessage(msg.recipient, null, msg.text, sentIntent, deliveryIntent)
                    }

                    // Acknowledge transmitted to tower
                    apiService.reportStatus(msg.uuid, status = "sent", deliveryConfirmed = false)
                    Log.i(TAG, "Dispatched message ${msg.uuid} to ${msg.recipient}")

                } catch (e: Exception) {
                    Log.e(TAG, "Error dispatching ${msg.uuid}: ${e.message}")
                    apiService.reportStatus(msg.uuid, status = "failed", error = e.message ?: "Unknown SMS exception")
                }
            }

            // Periodic heartbeat
            apiService.sendHeartbeat()

        } catch (e: Exception) {
            Log.e(TAG, "Queue polling cycle error: ${e.message}")
        }
    }
}
