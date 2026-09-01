# repartition vs partitionBy :

**Date:** 2024-03-03
**Author:** [Jayesh Pawar 💎](https://www.linkedin.com/in/jjayeshpawar)
**Repost:** Yes
**Post URL:** https://www.linkedin.com/feed/update/urn:li:activity:7169889674931396608/

---

## Content

repartition vs partitionBy :
🎯 Data storage 
repartition - data is saved in RAM 
partitionBy - data is stored in disk 
🎯 Syntax :
repartition - (can be applied on df directly)
df.repartition(3) => 3 partitions will be created
df.repartition("id") => *Optimal number of partitions will be created by spark and its NOT depend on no. of unique values in column.
df.repartition(3,"id") => 3 partitions will be created based on column "id" - Here instead of 3 if we specify number greater than no of unique values in id column then 3 partitions will have data and other partitions will be empty. 
partitionBy : (can only be used with df writer)
df.write.partitionBy("id")..... => *Here we cant specify number - No of partitions will be equal to no of unique values in column
🎯Shuffle :
repartition - Shuffling will be there
partitionBy - No shuffling of data as we are directly writing to the disk
🎯 Write :
repartition - df.repartition("id").write...
partitionBy - df.write.partitionBy("id")...

#pyspark

---

## Engagement

- 👍 Reactions: 113
- ❤️ Likes: 104
- 💬 Comments: 4
- 🔁 Reposts: 8

---

*Originally posted on [LinkedIn](https://www.linkedin.com/posts/jjayeshpawar_pyspark-activity-7169743736845942784-FkgV?utm_source=social_share_send&utm_medium=member_desktop_web&rcm=ACoAAGirrKUBRZEdEzDqvJIXwevKHtJzDbIrXeo)*