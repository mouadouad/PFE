import pandas as pd
import numpy as np

# 1. Chargement (Fichier issu du Bloc 1)
df = pd.read_csv("cesnet_points_clustered.csv")

df["bytes_reels"] = np.expm1(df["n_bytes"])

# 3. Création du RAN (Somme de tous les utilisateurs par 10 min)
# On groupe par 'timestamp' et par 'cluster' (Slice)
df_ran = df.groupby(["timestamp", "cluster"])["bytes_reels"].sum().reset_index()

# 4. Pivot pour avoir les Slices en colonnes
ran_pivot = df_ran.pivot(index="timestamp", columns="cluster", values="bytes_reels").fillna(0)
ran_pivot.columns = ["load_mMTC", "load_URLLC", "load_eMBB"] # Mapping supposé 0,1,2

# 5. Calcul de l'Énergie 
# Paramètres
P_STATIC = 157.0
P_VAR_MAX = 742.0

# Charge Totale
ran_pivot["total_load"] = ran_pivot["load_mMTC"] + ran_pivot["load_URLLC"] + ran_pivot["load_eMBB"]

# Rho (Taux de charge entre 0 et 1)
# On prend le MAX observé dans tout le dataset + 20% de marge
CAPACITY = ran_pivot["total_load"].max() * 1.2
ran_pivot["rho"] = ran_pivot["total_load"] / CAPACITY

# Watts
ran_pivot["energy_watts"] = P_STATIC + (ran_pivot["rho"] * P_VAR_MAX)

# Sauvegarde
ran_pivot.reset_index(inplace=True)
ran_pivot.to_csv("dataset_final_energy_gym.csv", index=False)

print("Fichier prêt pour l'IA : dataset_final_energy_gym.csv")
print(ran_pivot.head())
