<script lang="ts">
 import { Cpu, CircuitBoard, Radio, Wifi, Lightbulb, Cog, BatteryCharging, Activity, Triangle, Cable, Wrench, Gauge, Box, Bot, Package, LayoutGrid } from '@lucide/svelte';
 let { group = '', size = 18 }: { group?: string | null; size?: number } = $props();
 const icons = { 'dev-boards': CircuitBoard, 'microcontrollers-ics': Cpu, sensors: Radio, 'wireless-iot': Wifi, 'displays-leds': Lightbulb, 'motors-drivers': Cog, power: BatteryCharging, 'passive-components': Activity, semiconductors: Triangle, 'connectors-cables': Cable, prototyping: Wrench, 'tools-instruments': Gauge, '3d-printing-cnc': Box, 'robotics-kits': Bot, other: Package };
 let Icon = $derived(icons[group as keyof typeof icons] ?? LayoutGrid);
</script>
<Icon {size} strokeWidth={1.7} aria-hidden="true" />
