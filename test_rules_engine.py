#!/usr/bin/env python3
"""
Test script to verify the rules engine functionality.

This script demonstrates how to:
1. Load rules configuration
2. Initialize and run the rules engine
3. Feed sensor data and observe rule triggers
"""

import asyncio
import time
from pathlib import Path

from simulator.rules import load_rules_config, RulesEngine
from simulator.sensors.base import SensorReading


class MockMQTTPublisher:
    """Mock MQTT publisher for testing."""
    
    def __init__(self):
        self.published_messages = []
    
    def publish_raw(self, topic: str, payload: str, qos: int = 1) -> None:
        print(f"📡 MQTT Alert: {topic}")
        print(f"   Payload: {payload}")
        self.published_messages.append((topic, payload))


async def test_rules_engine():
    """Test the rules engine with simulated sensor data."""
    
    print("🔧 Testing Rules Engine")
    print("=" * 50)
    
    # Load rules configuration
    config_path = Path("config/rules_config.yaml")
    if not config_path.exists():
        print(f"❌ Rules config not found: {config_path}")
        print("   Run this from the project root directory.")
        return
    
    rules_config = load_rules_config(config_path)
    print(f"✅ Loaded {len(rules_config.rules)} rules")
    
    # Create mock MQTT publisher
    mqtt_publisher = MockMQTTPublisher()
    
    # Initialize rules engine
    rules_engine = RulesEngine(
        config=rules_config,
        mqtt_publisher=mqtt_publisher,
        scenario_engine=None,
    )
    
    print(f"✅ Rules engine initialized")
    
    # Start the rules engine
    await rules_engine.start()
    print("🚀 Rules engine started")
    
    try:
        # Simulate normal sensor readings first
        print("\n📊 Feeding normal sensor readings...")
        for i in range(5):
            # Normal heart rate
            reading = SensorReading(
                sensor_name="heart_rate",
                value=75.0 + i,  # 75-79 bpm (normal)
                unit="bpm",
                condition="normal",
                quality="good",
                fault_active=False,
                phase="normal",
                extra={}
            )
            
            rules_engine.process_sensor_reading(
                reading, "test_welder", "worker_safety", "test-device-001"
            )
            
            await asyncio.sleep(0.5)
        
        print("   No alerts expected for normal readings ✅")
        
        # Now simulate elevated heart rate to trigger rule
        print("\n🔥 Simulating elevated heart rate...")
        for i in range(10):
            reading = SensorReading(
                sensor_name="heart_rate",
                value=120.0 + i,  # 120+ bpm (elevated)
                unit="bpm",
                condition="warning",
                quality="good",
                fault_active=False,
                phase="fatigue",
                extra={}
            )
            
            rules_engine.process_sensor_reading(
                reading, "test_welder", "worker_safety", "test-device-001"
            )
            
            await asyncio.sleep(1.0)  # Wait for duration requirement
        
        # Simulate high temperature to trigger heat stress rule
        print("\n🌡️ Simulating heat stress...")
        for i in range(8):
            reading = SensorReading(
                sensor_name="skin_temperature",
                value=38.7 + (i * 0.1),  # Above 38.5°C
                unit="celsius",
                condition="critical",
                quality="good",
                fault_active=False,
                phase="heat_stress",
                extra={}
            )
            
            rules_engine.process_sensor_reading(
                reading, "test_welder", "worker_safety", "test-device-001"
            )
            
            await asyncio.sleep(1.0)
        
        # Simulate fall detection (high acceleration)
        print("\n💥 Simulating fall detection...")
        reading = SensorReading(
            sensor_name="accel_magnitude",
            value=4.5,  # Above 3.0g threshold
            unit="g",
            condition="critical",
            quality="good",
            fault_active=False,
            phase="emergency",
            extra={}
        )
        
        rules_engine.process_sensor_reading(
            reading, "test_welder", "worker_safety", "test-device-001"
        )
        
        # Wait a bit for rules to process
        await asyncio.sleep(2.0)
        
        # Show results
        print("\n📈 Rules Engine Results:")
        print("=" * 30)
        
        status = rules_engine.get_status()
        print(f"Rules evaluated: {status['enabled_rules']}")
        print(f"Violations recorded: {status['violation_count']}")
        print(f"MQTT alerts sent: {len(mqtt_publisher.published_messages)}")
        
        violations = rules_engine.get_recent_violations()
        print(f"\n📋 Recent violations ({len(violations)}):")
        for violation in violations:
            print(f"  • {violation['rule_name']} ({violation['severity']})")
            print(f"    Conditions: {', '.join(violation['triggered_conditions'])}")
        
        print("\n✅ Rules engine test completed successfully!")
        
    finally:
        # Stop the rules engine
        await rules_engine.stop()
        print("🛑 Rules engine stopped")


if __name__ == "__main__":
    asyncio.run(test_rules_engine())