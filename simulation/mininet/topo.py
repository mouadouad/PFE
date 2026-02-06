#!/usr/bin/env python
from mininet.net import Mininet
from mininet.node import Controller, RemoteController, OVSKernelSwitch
from mininet.topo import Topo
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import re
import time
import subprocess
import threading
import os
import requests
import json
from random import randint
import numpy as np
import pandas as pd
import csv
import random

previous_bytes_sent = {}
previous_packets_dropped= {}


def register_interfaces_to_ryu(switch, dpid, ryu_ip="172.18.0.10", ryu_port=8080):
    interfaces = [intf.name for intf in switch.intfList() if intf.name.startswith(switch.name + '-eth')]
    print("Interfaces détectées pour %s : %s" % (switch.name, interfaces))
    
    try:
        response = requests.post(
            f"http://{ryu_ip}:{ryu_port}/register_interfaces",
            json={"dpid": dpid, "interfaces": interfaces}
        )
        print("Réponse Ryu:", response.text)
    except Exception as e:
        print("Erreur en envoyant les interfaces à Ryu:", str(e))


def send_stats_to_ryu(dpid, stats, ryu_ip="172.18.0.10", ryu_port=8080):
    """Envoie les statistiques parsées à Ryu de manière structurée"""
    data = {
        "dpid": dpid,
        "timestamp": time.time(),
        "stats": stats
    }
    try:
        response = requests.post(
            f"http://{ryu_ip}:{ryu_port}/monitoring",
            json=data,
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status()  # Lève une exception si code HTTP != 2xx

        # On suppose que la réponse est bien au format JSON
        json_response = response.json()

        print("Stats envoyées à Ryu")
        return {
            'status': json_response.get('status'),
            'new_rates': json_response.get('new_rates'),
            'message': json_response.get('message')
        }

    except Exception as e:
        print("Erreur d'envoivers Ryu:", e)
        return { 
            'status': 'error',
            'new_rates': [],
            'message': str(e)
        }

def create_topology():
    """Crée la topologie du réseau avec support pour le slicing"""
    info('*** Création de la topologie réseau pour le slicing\n')
    
    # Création du réseau Mininet avec support QoS
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch, link=TCLink)
    
    # Ajout du contrôleur externe (Ryu)
    info('*** Ajout du contrôleur\n')
    c0 = net.addController('c0', controller=RemoteController, ip='172.18.0.10')

    # Ajout des switchs
    info('*** Ajout des switchs\n')
    s1 = net.addSwitch('s1')

    # Ajout des hôtes

    info('*** Ajout des hôtes (véhicules)\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
    h2 = net.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
    h3 = net.addHost('h3', mac='00:00:00:00:00:03', ip='10.0.0.3/24')
    
    # Ajout des liens avec paramètres de QoS pour le BDP
    info('*** Ajout des liens\n')
    
    net.addLink(h1, s1)
    net.addLink(h2, s1)

    net.addLink(h3, s1)

    #net.addLink(s1, s2)

    # Démarrage du réseau
    info('*** Démarrage du réseau\n')
    net.build()
    c0.start()
    s1.start([c0])
    register_interfaces_to_ryu(s1, dpid=1, ryu_ip="172.18.0.10")
     # Supprimer les configurations QoS OVS
    s1.cmd('ovs-vsctl clear port s1-eth1 qos')
    s1.cmd('ovs-vsctl clear port s1-eth2 qos')

    # Configurer tc sur les interfaces du switch
    configure_tc_queues_switch(s1)

    # --- Initialisation pour la simulation ---
    # Charger le DataFrame en dehors de la boucle pour ne pas recharger à chaque fois
    df = pd.read_csv("data.csv")
    df = df.tail(1500).reset_index(drop=True) 

    step_interval = random.uniform(0, 10) #time slot

    # Démarrer le processus de surveillance en temps réel dans un thread
    """monitor_thread1 = threading.Thread(target=monitor_tc_stats_realtime_loop, args=(s1, 1, 1))
    monitor_thread1.daemon = True 
    monitor_thread1.start()"""

    # --- Génération du trafic sur la durée totale de la simulation ---
    
    # Nombre d'entrées CSV à utiliser
    num_csv_entries_to_use = len(df['Slice Bandwidth (Mbps)']) 
    

    # Lancer les serveurs iperf une seule fois au début
    h1, h2, h3 = net.get('h1'), net.get('h2'), net.get('h3')


    info('*** Lancement des serveurs iperf sur les hôtes\n')
    for host in [h1, h2, h3]:
        host.cmd('iperf -s -u -p 5001 &') # mmtc
        host.cmd('iperf -s -u -p 5002 &') # URLLC
    time.sleep(2) 

    current_csv_idx = 0
    start_time_sim = time.time()
    df_urllc = df[df['Slice Type'] == 'URLLC']
    df_mmtc = df[df['Slice Type'] == 'mMTC']
    while current_csv_idx<=num_csv_entries_to_use:
        
        random_row_urllc = df_urllc.sample(n=1)
        random_row_mmtc = df_mmtc.sample(n=1)

        bw_urllc = float(random_row_urllc['Traffic Volume (bytes/sec)'].values[0]) * 8 / 1e6
        bw_mmtc = float(random_row_mmtc['Traffic Volume (bytes/sec)'].values[0]) * 8 / 1e6

        sla_urllc_latency = float(random_row_urllc['Latency Requirement (ms)'].values[0])
        sla_mmtc_latency = float(random_row_mmtc['Latency Requirement (ms)'].values[0])

        # Définir des tailles de paquets variées ou fixes
        pkt_size_urllc = 10  
        pkt_size_mmtc = 32 

        # Génération du trafic eMBB
        h1.cmd(f'iperf -c {h2.IP()} -u  -b {bw_urllc}M  -t 1 -p 5002 -S 0x28 &')
        h2.cmd(f'iperf -c {h1.IP()} -u  -b {bw_urllc}M  -t 1 -p 5002 -S 0x28 &')
        h3.cmd(f'iperf -c {h1.IP()} -u  -b {bw_urllc}M -t 1 -p 5002 -S 0x28 &')
        h1.cmd(f'iperf -c {h3.IP()} -u  -b {bw_urllc}M -t 1 -p 5002 -S 0x28 &')
        h3.cmd(f'iperf -c {h2.IP()} -u  -b {bw_urllc}M -t 1 -p 5002 -S 0x28 &')
        h2.cmd(f'iperf -c {h3.IP()} -u -b {bw_urllc}M -t 1 -p 5002 -S 0x28 &') 
        
        h1.cmd(f'iperf -c {h2.IP()} -u  -b {bw_mmtc}M -t 1 -p 5001 -S 0x50 &')
        h2.cmd(f'iperf -c {h1.IP()} -u  -b {bw_mmtc}M -t 1 -p 5001 -S 0x50 &')
        h3.cmd(f'iperf -c {h1.IP()} -u  -b {bw_mmtc}M -t 1 -p 5001 -S 0x50 &')
        h1.cmd(f'iperf -c {h3.IP()} -u -b {bw_mmtc}M -t 1 -p 5001 -S 0x50 &')
        h3.cmd(f'iperf -c {h2.IP()} -u  -b {bw_mmtc}M -t 1 -p 5001 -S 0x50 &')
        h2.cmd(f'iperf -c {h3.IP()} -u  -b {bw_mmtc}M -t 1 -p 5001 -S 0x50 &') 
        
        
        # Attendre la durée de l'intervalle
        stats_to_send = collect_tc_stats(s1, 1, 
            sla_urllc_latency, 
            sla_mmtc_latency
        ) 
                
        current_csv_idx += 1
        time.sleep(step_interval)
        
        # Tuer les processus iperf clients en cours
        for host in [h1, h2, h3]:
            host.cmd('pkill -f "iperf -c"') 

    info('*** Nettoyage des processus iperf serveurs\n')
    for host in [h1, h2, h3]:
        host.cmd('pkill iperf') 

    print("Fin de génération du trafic.")
    
    # ... (le reste du code) ...

"""def monitor_tc_stats_realtime_loop(switch, dpid, interval_seconds=1):
   
    while True:
        try:
            collect_tc_stats(switch, dpid)
            time.sleep(interval_seconds)
        except Exception as e:
            print(f"Erreur dans le thread de monitoring pour {switch.name}: {e}")
            break

"""


def configure_tc_queues_switch(switch):
    """Configure les files d'attente avec tc sur les interfaces du switch."""
    # Obtenir les interfaces du switch (par exemple, s1-eth1, s1-eth2)
    intfs = switch.intfList()
    for intf in intfs:
        if intf.name.startswith(switch.name + '-eth'):
            intf_name = intf.name
            switch.cmd('tc qdisc del dev %s root' % intf_name)
            switch.cmd('tc qdisc add dev %s root handle 1: htb default 12' % intf_name)
            switch.cmd('tc class add dev %s parent 1: classid 1:1 htb rate 100mbit ceil 50mbit' % intf_name) 
            
            switch.cmd('tc class add dev %s parent 1:1 classid 1:10 htb rate 50mbit ceil 50mbit' % intf_name)
            switch.cmd('tc class add dev %s parent 1:1 classid 1:11 htb rate 50mbit ceil 50mbit' % intf_name)
            switch.cmd('tc qdisc add dev %s parent 1:10 handle 10:  pfifo limit 1' % intf_name)
            switch.cmd('tc qdisc add dev %s parent 1:11 handle 11:  pfifo limit 1' % intf_name)

            switch.cmd('tc filter add dev %s parent 1: protocol ip prio 1 u32 match u8 0x28 0xff at 1 flowid 1:10' % intf_name)
            switch.cmd('tc filter add dev %s parent 1: protocol ip prio 2 u32 match u8 0x50 0xff at 1 flowid 1:11' % intf_name)

            switch.cmd('tc filter add dev %s parent 1: protocol ip prio 1 u32 match ip protocol 17 0xff match u16 0x1389 0xffff at 22 flowid 1:10' % intf_name)
            switch.cmd('tc filter add dev %s parent 1: protocol ip prio 2 u32  match ip protocol 17 0xff match u16 0x138a 0xffff at 22 flowid 1:11  ' % intf_name)
            output = switch.cmd("tc filter show dev {} parent 1:".format(intf_name))

            print ("Files tc configurées sur %s" % intf_name)
            print(output)
            


            
def collect_tc_stats(switch, dpid, sla_latency_urllc, sla_latency_mmtc):
    global previous_bytes_sent
    global previous_packets_dropped
    stats = []
    
    intfs = switch.intfList()
    
    for intf in intfs:
        if intf.name.startswith(switch.name + '-eth') and intf.name != switch.name + '-eth4':
            intf_name = intf.name
            output = switch.cmd('tc -s class show dev %s' % intf_name)
            timestamp = time.time()
            lines = output.strip().split('\n')
            current_class = None
            parent_rate = 0
            
            # Collecte du parent rate
            for line in lines:
                line = line.strip()
                if line.startswith("class"):
                    parts = line.split()
                    if len(parts) >= 3:
                        current_class = parts[2] 
                if 'class htb' in line and current_class in line and current_class =="1:1":
                    tokens = line.split()
                    if 'rate' in tokens:
                        rate_index = tokens.index('rate')
                        rate_str = tokens[rate_index + 1]  
                        parent_rate = int(''.join(filter(str.isdigit, rate_str)))
                        break
            
            # Collecte des données de toutes les classes
            class_data = {}
            current_class = None
            
            for line in lines:
                line = line.strip()
                if line.startswith("class"):
                    parts = line.split()
                    if len(parts) >= 3:
                        current_class = parts[2]
                        if current_class not in class_data:
                            class_data[current_class] = {
                                'bytes_sent': 0,
                                'backlog_bytes': 0,
                                'dropped': 0,
                                'rate': 0,
                                'pkts_sent': 0
                            }

                if 'class htb' in line and current_class in line:
                    tokens = line.split()
                    if 'rate' in tokens:
                        rate_index = tokens.index('rate')
                        rate_str = tokens[rate_index + 1]
                        numeric_rate = int(''.join(filter(str.isdigit, rate_str)))
                        class_data[current_class]['rate'] = numeric_rate

                if line.startswith("Sent") and current_class:
                    match = re.search(r'Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt', line)
                    if match:
                        bytes_sent = int(match.group(1))
                        pkts_sent = int(match.group(2))
                        class_data[current_class]['bytes_sent'] = bytes_sent
                        class_data[current_class]['pkts_sent'] = pkts_sent
                        

                if 'backlog' in line and current_class:
                    match = re.search(r'backlog\s+(\d+)b', line)
                    if match:
                        class_data[current_class]['backlog_bytes'] = int(match.group(1))

                if 'dropped' in line and current_class:
                    dropped_part = line.split('dropped')[1]
                    dropped = int(dropped_part.split(',')[0].strip())
                    class_data[current_class]['dropped'] = dropped

            # TRAITEMENT DANS L'ORDRE SPÉCIFIQUE : 1:10, 1:11
            ordered_classes = ['1:10', '1:11']
            
            # Variables pour le dataset (réinitialisées pour chaque interface)
            nbre_demands_urllc = 0
            latency_urllc = 0
            nbre_demands_dropped_urllc = 0
            debit_urrlc = 0
            nbre_demands_mmtc = 0
            latency_mmtc = 0
            nbre_demands_dropped_mmtc = 0
            debit_mmtc= 0
            
            for cls in ordered_classes:
                if cls in class_data:
                    data = class_data[cls]
                    key = (intf_name, cls)
                    prev = previous_bytes_sent.get(key, data['bytes_sent'])
                    prev_dropped = previous_packets_dropped.get(key, data['dropped'])

                    actual_bytes_sent = data['bytes_sent'] - prev 
                    actual_dropped = data['dropped'] - prev_dropped

                    previous_bytes_sent[key] = data['bytes_sent']
                    previous_packets_dropped[key] = data['dropped']

                    latency = 0
                    throughput = (actual_bytes_sent * 8) / 1e6 
                    actual_rate = data['rate']
                    backlog_delay = (data['backlog_bytes'] * 8) / (actual_rate * 1e6)  # ms
                    transmission_delay = (actual_bytes_sent * 8) / (actual_rate * 1e6)   # ms
                    latency = transmission_delay *1000
                    avg_pkt_size = data['bytes_sent']/ (data['pkts_sent'] if data['pkts_sent'] > 0 else 1) 
                    # Attribution selon la classe dans l'ordre
                    if cls == '1:10':  # URLLC
                        sla = sla_latency_urllc
                        nbre_demands_urllc = actual_bytes_sent + actual_dropped + data['backlog_bytes']
                        latency_urllc = latency
                        nbre_demands_dropped_urllc = actual_dropped
                        debit_urrlc = throughput
                    elif cls == '1:11':  # mmtc
                        sla = sla_latency_mmtc
                        nbre_demands_mmtc = actual_bytes_sent + actual_dropped + data['backlog_bytes']
                        latency_mmtc = latency
                        nbre_demands_dropped_mmtc = actual_dropped
                        debit_mmtc = throughput
                    scale = np.random.choice([0, 1, 2, 3, 4], p=[1/5]*5)
                    if scale==1:
                        factor= 10
                    else:
                        factor=1
                    nbre_demands =  data['pkts_sent'] + actual_dropped + (data['backlog_bytes']/ avg_pkt_size if avg_pkt_size > 0 else 1)
                    nbre_demands_bytes = ((actual_dropped * avg_pkt_size) + actual_bytes_sent + data['backlog_bytes'])*8/1e6  # en Mbits
                    
                    # Ajouter à stats dans l'ordre
                    stats.append({
                        'interface': intf_name,
                        'class': cls,
                        'dropped': actual_dropped/nbre_demands,
                        'rate': actual_rate,
                        'throughput': throughput,
                        'latency': latency,
                        'parent_rate': parent_rate,
                        'nbre_demands': nbre_demands*factor,
                        'nbre_demands_bytes': nbre_demands_bytes*factor,
                        'sla_latency': sla,
                    })
                    
            
                    print("Interface %s classe %s transmis %d backlog %d rate %d debit %.3f, latence %.3f, packet size %.3f" % (
                            intf_name, cls, actual_bytes_sent,data['backlog_bytes'], data['rate'], throughput, latency, avg_pkt_size
                    ))

    
                    
    if dpid == 1:
        new_rates = send_stats_to_ryu(dpid, stats)['new_rates']
        stats = []


    #réallocation des rates
    if new_rates:
        for stat in new_rates:
            interface = stat['id']
            #classid = interface.split("-")[0]
            rate_bps1 = stat['rates'][0]
            rate_bps2 = stat['rates'][1]
            rate_mbit1 = int(rate_bps1)
            rate_mbit2 = int(rate_bps2)
            
            rate_str1 = "{}mbit".format(rate_mbit1)
            rate_str2 = "{}mbit".format(rate_mbit2)

            """rate_str_child = "{}mbit".format((rate//2))
            switch.cmd('tc class change dev %s classid 1:1 htb rate %s' % (interface, rate_str))"""
            switch.cmd('tc class change dev %s classid 1:10  htb rate %s ceil %s' % (interface, rate_str1,  rate_str1))
            switch.cmd('tc class change dev %s classid 1:11  htb rate %s ceil %s' % (interface, rate_str2, rate_str2))

    return stats

def monitor_tc_stats_realtime(switch, dpid, duration=60):
   
    import threading
    import time
    
    def monitor_loop():
        for i in range(duration):
            print(f"\n=== MONITORING ITERATION {i+1}/{duration} ===")
            collect_tc_stats(switch, dpid)
            time.sleep(1)
    
    monitor_thread = threading.Thread(target=monitor_loop)
    monitor_thread.start()
    return monitor_thread

def main():
    """Fonction principale"""
    setLogLevel('info')
    
    # Créer la topologie
    create_topology()
    

if __name__ == '__main__':
    main()
