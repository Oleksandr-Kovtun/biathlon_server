import serial
import requests
import time

# Configuration settings
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 19200
SERVER_URL = 'http://127.0.0.1:80/feed'

def main():
    print(f"Connecting to {SERIAL_PORT} at {BAUD_RATE} bps...")
    
    try:
        # Explicitly configure all COM port parameters
        ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,    # Data bit: 8
            parity=serial.PARITY_NONE,    # Parity: none
            stopbits=serial.STOPBITS_ONE, # Stop bit: 1
            xonxoff=False,                # Software flow control: none
            rtscts=False,                 # Hardware (RTS/CTS) flow control: none
            dsrdtr=False,                 # Hardware (DSR/DTR) flow control: none
            timeout=1
        )
        print("Connected! Listening for KES messages...")
        
        while True:
            if ser.in_waiting > 0:
                # KES protocol messages end with carriage return
                raw_data = ser.read_until(b'\r')
                
                try:
                    # Decode the byte string to text and remove whitespace
                    msg = raw_data.decode('ascii').strip()
                    
                    if msg:
                        # Forward the valid message to the web server
                        requests.get(SERVER_URL, params={'msg': msg}, timeout=2)
                except Exception as e:
                    # Log decoding or network errors
                    print(f"Error processing data: {e}")
                    
    except Exception as e:
        # Log port connection errors
        print(f"Failed to open port {SERIAL_PORT}: {e}")

if __name__ == '__main__':
    main()
