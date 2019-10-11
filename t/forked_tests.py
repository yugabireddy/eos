#!/usr/bin/env python3

import json
import time

from logger import LoggingLevel, WriterConfig, ScreenWriter, FileWriter, Logger
from service import Service, Cluster, CommandLineArguments

max_seen_blocknum = 1

def wait_get_block(cluster, block_num, print):
    tries = 10
    global max_seen_blocknum
    while (max_seen_blocknum < block_num) and tries > 0:
        # inforsp = cluster.get_cluster_info()
        ix = cluster.get_cluster_info()
        inforsp = json.loads(ix.response.text)
        max_seen_blocknum = inforsp["result"][0][1]["head_block_num"]
        if (max_seen_blocknum < block_num):
            time.sleep(0.5 * (block_num - max_seen_blocknum + 1))
        tries = tries - 1
    assert tries > 0, "failed to get block %d, max_seen_blocknum is %d" % (block_num, max_seen_blocknum)
    return json.loads(cluster.get_block(block_num).response.text)

def isInSync(cluster, minNodeNum):
    tries = 5
    while tries > 0:
        ix = cluster.get_cluster_info()
        inforsp = json.loads(ix.response.text)
        node_count = len(inforsp["result"])
        head_block_id = inforsp["result"][0][1]["head_block_id"]
        insync = True
        count = 0
        min_block_num = 0
        max_block_num = 0
        for i in range(0, node_count):
            if "head_block_id" in inforsp["result"][i][1]:
                count = count + 1
                block_num = inforsp["result"][i][1]["head_block_num"]
                if min_block_num == 0 or block_num < min_block_num:
                    min_block_num = block_num
                if max_block_num == 0 or block_num > max_block_num:
                    max_block_num = block_num
                if head_block_id != inforsp["result"][i][1]["head_block_id"]:
                    insync = False
        if insync and count >= minNodeNum:
            return True,min_block_num,max_block_num
        if max_block_num - min_block_num > 2: # can't be in sync...
            break
        tries = tries - 1
        time.sleep(0.1)
    return False,min_block_num,max_block_num

def verifyProductionRound(cluster, exp_prod, print):

    # inforsp = cluster.get_cluster_info()
    ix = cluster.get_cluster_info()
    inforsp = json.loads(ix.response.text)
    head_num = inforsp["result"][0][1]["head_block_num"]
    print("head_num is %d" % (head_num))

    curprod = "(none)"
    while not curprod in exp_prod:
        block = wait_get_block(cluster, head_num, print=print)
        curprod = block["producer"]
        print("head_num is %d, producer %s, waiting for schedule change" % (head_num, curprod))
        head_num = head_num + 1

    seen_prod = {curprod : 1}
    verify_end_num = head_num + 12 * len(exp_prod)
    for blk_num in range(head_num, verify_end_num):
        block = wait_get_block(cluster, blk_num, print=print)
        curprod = block["producer"]
        print("block %d, producer %s, %d blocks remain to verify" % (blk_num, curprod, verify_end_num - blk_num - 1))
        assert curprod in exp_prod, "producer %s is not expected in block %d" % (curprod, blk_num)
        seen_prod[curprod] = 1

    if len(seen_prod) == len(exp_prod):
        print("verification succeed")
        return True

    print("verification failed, #seen_prod is %d, expect %d" % (len(seen_prod), len(exp_prod)))
    return False


def main():
    buffered_color_config = WriterConfig(buffered=True, monochrome=False, threshold="DEBUG")
    unbuffered_mono_config = WriterConfig(buffered=False, monochrome=True, threshold="TRACE")
    logger = Logger(ScreenWriter(config=buffered_color_config), FileWriter(filename="mono.log", config=unbuffered_mono_config))
    service = Service(logger=logger)

    print_info = lambda msg: logger.info(msg=msg, flush=True)

    print_info(">>> Forked test starts.")
    total_nodes = 3
    cluster = Cluster(service=service, total_nodes=total_nodes, total_producers=total_nodes * 2 + 1, producer_nodes=total_nodes, dont_vote=True, topology="bridge", center_node_id=1)

    stake_amount = "75000001.0000 SYS"
    cluster.create_account(node_id=0, creator="eosio", name="tester1", stake_cpu=stake_amount, stake_net=stake_amount, buy_ram_bytes=1048576, transfer=True)

    node_prod = cluster.nodes[0]["producers"] + cluster.nodes[2]["producers"]
    node_prod.remove("eosio")
    print_info("vote for following producers (from node 0 and node 2):")
    for p in node_prod:
        print_info(p)

    cluster.vote_for_producers(node_id=0, voter="tester1", voted_producers=node_prod)

    res,min_blk,max_blk = isInSync(cluster, 3)
    assert res, "cluster is not in sync"

    print_info("verfiying schedule...")
    verifyProductionRound(cluster, node_prod, print=print_info)

    res,min_blk,max_blk = isInSync(cluster, 3)
    last_known_insync_blk = max_blk
    assert res, "cluster is not in sync"

    # kill bridge node with signal 9
    cluster.stop_node(node_id=1, kill_sig=9)

    time.sleep(1)

    res,min_blk0,max_blk0 = isInSync(cluster, 2)
    assert res == False, "cluster should not in sync"

    print_info("wait until 2 forks has different lengths")
    tries = 60
    while tries > 0:
        time.sleep(1)
        res,min_blk1,max_blk1 = isInSync(cluster, 2)
        print_info("min head block num is %d, max head block num is %d" % (min_blk1, max_blk1))
        assert res == False, "cluster should not in sync"
        if max_blk1 >= min_blk1 + 13 and min_blk1 > min_blk0 and max_blk1 > max_blk0:
            break
        tries = tries - 1

    assert min_blk1 > min_blk0 and max_blk1 > max_blk0, "nodes are not advancing"

    print_info("try to restart bridge node")
    time.sleep(1)

    # restart node with --delete-all-blocks
    cluster.start_node(node_id=1, extra_args="--delete-all-blocks")

    tries = 30
    while tries > 0:
        res,min_blk,max_blk = isInSync(cluster, 3)
        if res:
            break
        tries = tries - 1
        time.sleep(1)

    assert tries > 0, "cluster is not in sync"

    time.sleep(1)

    res,min_blk2,max_blk2 = isInSync(cluster, 3)
    assert min_blk2 > min_blk, "cluster is not advancing"

    print_info(">>> Nodes in-synced, head block num is %d" % (max_blk2))

    for block_num in range(last_known_insync_blk, max_blk2 + 1):
        blk0 = cluster.get_block(block_num, node_id=0).response.text
        blk1 = cluster.get_block(block_num, node_id=1).response.text
        blk2 = cluster.get_block(block_num, node_id=2).response.text
        if not (blk0 == blk1 and blk1 == blk2):
            print_error("block verification failed at block %d" % (block_num))
            assert False

    print_info(">>> Fork successfully resolved, head block num is %d" % (max_blk2))

if __name__ == '__main__':
    main()
