# -*- coding: utf-8 -*-
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import arp
from ryu.lib.packet import ether_types, packet, ethernet, ipv4, udp,tcp
from ryu.lib import hub
from ryu.app.wsgi import WSGIApplication, ControllerBase, Response, route
import subprocess
import re
import time
import os
#from darts.models import NHiTSModel

from ryu.app.wsgi import ControllerBase, route
from webob import Response
import json


# -*- coding: utf-8 -*-
class InterfaceAPI(ControllerBase):
    switch_interfaces = {}  # Dictionnaire global [dpid] -> [interfaces]
    monitoring_data = {} 
    previous_stats = {}
    stats = []
    stats_todrl = [] 
    new_rates = []  # Pour stocker les nouvelles allocations de bande passante
    ports = []
    @route('interface', '/register_interfaces', methods=['POST'])
    def register_interfaces(self, req, **kwargs):
        try:
            data = req.json if req.body else {}
            dpid = int(data.get('dpid'))
            interfaces = data.get('interfaces', [])

            InterfaceAPI.ports.append(interfaces)

            return Response(content_type='application/json',
                            body=json.dumps({'status': 'ok'}))
        except Exception as e:
            return Response(status=500, body=str(e))
    @route('interface', '/getports', methods=['GET'])
    def get_ports(self, req, **kwargs):
        if InterfaceAPI.ports:
            body = json.dumps({'ports': InterfaceAPI.ports})
            return Response(content_type='application/json', body=body)
        else:
            time.sleep(3)
            body = json.dumps({'ports': InterfaceAPI.ports})
            return Response(content_type='application/json', body=body)
        
    @route('interface', '/monitoring', methods=['POST'])
    def monitoring(self, req, **kwargs):
        congested = False  
        total_drop = 0
        total_latency = 0
        total_demands = 0
        try:
            data = req.json if req.body else {}
            dpid = int(data.get('dpid'))
            
            timestamp = data.get('timestamp', time.time())
            interfaces_stats = data.get('stats', [])
            # Stocker les données de monitoring
            InterfaceAPI.monitoring_data[dpid] = {
                'timestamp': timestamp,
                'interfaces': interfaces_stats
            }
            
            print("[API] Donnees de monitoring recues pour DPID %s à %s" % (dpid, time.ctime(timestamp)))
            if dpid not in InterfaceAPI.previous_stats:
                InterfaceAPI.previous_stats[dpid] = {}

            per_interface = {}
            for stat in interfaces_stats:
                
                #key = (stat['interface'], stat['class'])
                iface = stat['interface']
                taille = len(interfaces_stats)
                #prev_dropped = InterfaceAPI.previous_stats[dpid].get(key, 0)
                classe = stat['class']
                rate = stat.get('rate', 0)
                latency = stat.get('latency', 0)
                throughput = stat.get('throughput', 0)
                drop_rate = stat['dropped']
                total_drop += drop_rate
                total_latency += latency
                parent_rate = stat.get('parent_rate', 0)*1000000 
                nbre_demands = stat.get('nbre_demands', 0)
                nbre_demands_bytes = stat.get('nbre_demands_bytes', 0)
                total_demands += nbre_demands
                InterfaceAPI.stats.append({
                        'timestamp': timestamp,
                        'dpid': int(dpid),
                        'interface': stat['interface'],
                        'class': classe,
                        'dropped': drop_rate,
                        'latency': latency,
                        'throughput': throughput,
                        'rate': rate,
                        'parent_rate': parent_rate,
                        'nbre_demands': nbre_demands,
                        'nbre_demands_bytes': nbre_demands_bytes,
                        'sla_latency': stat.get('sla_latency', 0)
                })

                #InterfaceAPI.previous_stats[dpid][key] = stat['dropped']

            InterfaceAPI.stats_todrl = [dict(entry) for entry in InterfaceAPI.stats]
            InterfaceAPI.stats = []
            #if (total_drop/total_demands) > 0.01 or total_latency/taille>2.7:
            if InterfaceAPI.new_rates:
                    print("mis à jour des rates")
                    body = json.dumps({
                        'status': 'ok', 
                        'message': 'Monitoring data received',
                        'new_rates': InterfaceAPI.new_rates
                    })

                    # vider après avoir préparé la réponse
                    InterfaceAPI.new_rates = []
                    total_drop = 0
                    return Response(content_type='application/json', body=body)
                    
            return Response(content_type='application/json',
                                body=json.dumps({
                                    'status': 'ok', 
                                    'message': 'Monitoring data received'
                            }))
        except Exception as e:
            print("[API ERROR] Erreur lors du traitement des données de monitoring:", str(e))
            return Response(status=500, body=str(e))
            
           
    @route('interface', '/getstats', methods=['POST'])
    def getstats(self, req, **kwargs):
        try:
            data = req.json if req.body else {}
            stats_interfaces = InterfaceAPI.stats_todrl
            if stats_interfaces:
                print("[API] Stats disponibles pour les interfaces")
                return Response(content_type='application/json',
                                    body=json.dumps({
                                        'status': 'ok', 
                                        'stats': stats_interfaces
                                    }))
                
            else:
                return Response(content_type='application/json',
                                    body=json.dumps({
                                        'status': 'error', 
                                        'message': 'Stats indisponibles'
                                    }))
        except Exception as e:
            print("[API ERROR] Erreur lors du traitement des données:", str(e))
            return Response(status=500, body=str(e))
        
    @route('interface', '/setaction', methods=['POST'])
    def setaction(self, req, **kwargs):
        try:
            data = req.json if req.body else {}
            InterfaceAPI.new_rates.append(data)
            
            return Response(content_type='application/json',
                            body=json.dumps({
                                'status': 'ok'
            }))
                
        except Exception as e:
            print("[API ERROR] Erreur lors du traitement des données:", str(e))
            return Response(status=500, body=str(e))
        
# -*- coding: utf-8 -*-
class SwitchWithStats(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {
        'wsgi': WSGIApplication
    }
    def __init__(self, *args, **kwargs):
        super(SwitchWithStats, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}  

        # Configuration pour la réallocation
        self.drop_threshold = 10  # Seuil de paquets droppés
        self.switch_interfaces = {}  # Mapping dpid -> interfaces
        self.current_bandwidth = {
            'high_priority': 1000000,  # 1Mbps en bytes/sec
            'low_priority': 1000000    # 1Mbps en bytes/sec
        }

        wsgi = kwargs['wsgi']
        wsgi.register(InterfaceAPI)

        self.switch_interfaces = InterfaceAPI.switch_interfaces
        self.monitoring_data = InterfaceAPI.monitoring_data
        
        self.logger.info("Initialisation du contrôleur avec API de monitoring")

        # Démarrer le thread de monitoring périodique
        self.monitor_thread = hub.spawn(self._periodic_analysis)

    
    def _periodic_analysis(self):
        """Thread périodique qui analyse les données de monitoring reçues"""
        while True:
            if self.monitoring_data:
                self.logger.info("=== STATISTIQUES ===")
                
                for dpid, data in self.monitoring_data.items():
                    timestamp = data['timestamp']
                    interfaces_stats = data['interfaces']
                    
                    # Afficher les statistiques détaillées
                    self._display_detailed_stats(dpid, interfaces_stats, timestamp)
                    
            
            hub.sleep(1)  


    def _display_detailed_stats(self, dpid, interfaces_stats, timestamp):
        """Affiche les statistiques de manière détaillée"""
        self.logger.info("Switch DPID %s - Statistiques à %s", dpid, time.ctime(timestamp))
        self.logger.info("-" * 60)
        
        for entry in interfaces_stats:
           

            print("Interface: %s | Classe: %s | Paquets perdus: %d|" % (
                entry['interface'], entry['class'], entry['dropped']))
        

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.info('Datapath enregistré: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.info('Datapath non-enregistré: %016x', datapath.id)
                del self.datapaths[datapath.id]


    def _monitor_queues(self):
        """Thread périodique : envoie des requêtes de statistiques chaque seconde."""
        while True:
            for dpid in self.datapaths.keys():
                self._collect_and_analyze_stats(dpid)
            hub.sleep(2)  # Vérification toutes les 2 secondes

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Installer une règle par défaut : envoyer tous les paquets au contrôleur
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        dst = eth.dst
        src = eth.src

        if eth.ethertype in [ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6]:
            return

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)

        actions = [parser.OFPActionOutput(out_port)]

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data)
        datapath.send_msg(out)
    def _reallocate_resources(self, dpid, total_dropped):
        """Effectue la réallocation des ressources"""
        switch_name = "s%s" % dpid

        adjustment_factor = min(float(total_dropped) / self.drop_threshold, 3.0)

        new_high_bw = int(self.current_bandwidth['high_priority'] * (1 + adjustment_factor * 0.5))
        new_low_bw = max(500000, int(self.current_bandwidth['low_priority'] * (1 - adjustment_factor * 0.3)))

        self.logger.info('Réallocation pour %s: HP=%.1fMbps, LP=%.1fMbps' % (
            switch_name, new_high_bw / 1000000.0, new_low_bw / 1000000.0))

        for intf in self.switch_interfaces[dpid]:
            if intf != "lo":
                self._reconfigure_tc_classes(intf, new_high_bw, new_low_bw)

        self.current_bandwidth['high_priority'] = new_high_bw
        self.current_bandwidth['low_priority'] = new_low_bw

   


                