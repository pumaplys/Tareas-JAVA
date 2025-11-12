package practicas;

import java.util.Arrays;

public class arraydearrays {
    public static void main(String[] args) {
        int[] primerafila = {1,2,3};
        int[] segundafila = {4,5,6};

        int[][] tabla = {primerafila, segundafila};
        for (int[] tablacompleta : tabla) {
            System.out.println(Arrays.toString(tablacompleta));
        }
    }
}
